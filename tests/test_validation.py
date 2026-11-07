from copy import deepcopy

import pytest

from backend.validation import ModelJSONError, enrich_criteria, parse_model_json, validate_response


@pytest.fixture
def conversation():
    return {"conversation_id": "test-dialogue", "transcript": [
        {"message_id": "m1", "speaker": "client", "timestamp": "00:00", "text": "Какой тариф?", "offset_sec": None},
        {"message_id": "m2", "speaker": "operator", "timestamp": "00:01", "text": "Здравствуйте! Я Анна. Тариф «Базовый» — 100 рублей.", "offset_sec": None},
        {"message_id": "m3", "speaker": "system", "timestamp": "00:02", "text": "Проверка личности пройдена.", "offset_sec": None},
        {"message_id": "m10", "speaker": "bot", "timestamp": "00:03", "text": "Чат закрыт.", "offset_sec": None},
    ]}


@pytest.fixture
def checklist():
    return {"criteria": [
        {"criterion_id": "contact", "type": "yes_no", "active": True, "allow_na": False},
        {"criterion_id": "identification", "type": "yes_no", "active": True, "allow_na": True,
         "na_condition": "Запрос не требовал идентификации."},
        {"criterion_id": "consultation", "type": "scale", "active": True, "allow_na": False,
         "scale": {"0": "Нет ответа", "15": "Частичный ответ", "20": "Полный ответ"}},
        {"criterion_id": "violation", "type": "flag", "active": True, "allow_na": False},
        {"criterion_id": "inactive", "type": "yes_no", "active": False, "allow_na": False},
    ]}


@pytest.fixture
def payload():
    def row(cid, answer, message_id, quote, reasoning="Действие подтверждено репликой.", checked_range=None):
        return {"criterion_id": cid, "answer": answer, "reasoning": reasoning,
                "evidence": [{"message_id": message_id, "quote": quote}], "checked_range": checked_range}
    return {"conversation_id": "test-dialogue", "criteria": [
        row("contact", "yes", "m2", "Здравствуйте! Я Анна."),
        row("identification", "na", "m1", "Какой тариф?", "Запрос не требовал идентификации. Клиент задал общий вопрос."),
        row("consultation", "20", "m2", "Тариф «Базовый» — 100 рублей."),
        row("violation", "no", "m10", "Чат закрыт.", checked_range={"from": "m1", "to": "m10"}),
    ]}


def codes(payload, conversation, checklist, cid=None):
    errors = validate_response(payload, conversation, checklist)
    return {e["code"] for e in errors if cid is None or e["criterion_id"] == cid}


def test_valid_payload_and_no_mutation(payload, conversation, checklist):
    before = deepcopy((payload, conversation, checklist))
    assert validate_response(payload, conversation, checklist) == []
    assert (payload, conversation, checklist) == before


def test_unknown_message_id(payload, conversation, checklist):
    payload["criteria"][0]["evidence"][0]["message_id"] = "m999"
    assert "UNKNOWN_EVIDENCE_MESSAGE" in codes(payload, conversation, checklist, "contact")


def test_quote_from_other_message_is_rejected(payload, conversation, checklist):
    payload["criteria"][0]["evidence"][0]["message_id"] = "m1"
    assert "QUOTE_NOT_IN_MESSAGE" in codes(payload, conversation, checklist, "contact")


def test_quote_is_one_message_not_across_messages(payload, conversation, checklist):
    payload["criteria"][0]["evidence"][0]["quote"] = "Какой тариф? Здравствуйте!"
    assert "QUOTE_NOT_IN_MESSAGE" in codes(payload, conversation, checklist, "contact")


def test_quote_normalizes_case_space_quotes_dashes(payload, conversation, checklist):
    payload["criteria"][2]["evidence"][0]["quote"] = 'ТАРИФ "БАЗОВЫЙ" -\n 100\u00a0РУБЛЕЙ.'
    assert validate_response(payload, conversation, checklist) == []


@pytest.mark.parametrize("quote", ["", "  ", None, 12, {"text": "x"}])
def test_quote_requires_nonempty_string(payload, conversation, checklist, quote):
    payload["criteria"][0]["evidence"][0]["quote"] = quote
    assert "QUOTE_REQUIRED" in codes(payload, conversation, checklist, "contact")


def test_quote_length_is_limited(payload, conversation, checklist):
    conversation["transcript"][1]["text"] = "a" * 201
    payload["criteria"][0]["evidence"][0]["quote"] = "a" * 201
    assert "QUOTE_TOO_LONG" in codes(payload, conversation, checklist, "contact")


@pytest.mark.parametrize("answer", ["17", "20.0", 20, True, None, ["20"]])
def test_disallowed_scale_step(payload, conversation, checklist, answer):
    payload["criteria"][2]["answer"] = answer
    assert codes(payload, conversation, checklist, "consultation") & {"INVALID_SCALE_STEP", "INVALID_ANSWER"}


def test_na_not_allowed(payload, conversation, checklist):
    payload["criteria"][0]["answer"] = "na"
    assert "NA_NOT_ALLOWED" in codes(payload, conversation, checklist, "contact")


def test_flag_never_allows_na_even_bad_config(payload, conversation, checklist):
    payload["criteria"][3]["answer"] = "na"
    checklist["criteria"][3]["allow_na"] = True
    assert "NA_NOT_ALLOWED" in codes(payload, conversation, checklist, "violation")


def test_no_without_range(payload, conversation, checklist):
    payload["criteria"][0].update(answer="no", evidence=[], checked_range=None)
    assert "CHECKED_RANGE_REQUIRED" in codes(payload, conversation, checklist, "contact")


def test_no_with_range_can_omit_evidence(payload, conversation, checklist):
    payload["criteria"][0].update(answer="no", evidence=[], checked_range={"from": "m1", "to": "m2"})
    assert validate_response(payload, conversation, checklist) == []


def test_low_positive_scale_needs_range_and_evidence(payload, conversation, checklist):
    payload["criteria"][2].update(answer="15", evidence=[], checked_range=None)
    assert {"CHECKED_RANGE_REQUIRED", "EVIDENCE_REQUIRED"} <= codes(payload, conversation, checklist, "consultation")


def test_zero_scale_with_range_can_omit_evidence(payload, conversation, checklist):
    payload["criteria"][2].update(answer="0", evidence=[], checked_range={"from": "m1", "to": "m10"})
    assert validate_response(payload, conversation, checklist) == []


@pytest.mark.parametrize("checked_range,expected", [
    ({"from": "m1", "to": "m999"}, "UNKNOWN_RANGE_MESSAGE"),
    ({"from": "m10", "to": "m2"}, "REVERSED_CHECKED_RANGE"),
    ({"from": [], "to": "m2"}, "UNKNOWN_RANGE_MESSAGE"),
    (["m1", "m2"], "INVALID_CHECKED_RANGE"),
])
def test_invalid_range(payload, conversation, checklist, checked_range, expected):
    payload["criteria"][3]["checked_range"] = checked_range
    assert expected in codes(payload, conversation, checklist, "violation")


def test_ranges_use_transcript_order_not_lexicographic_ids(payload, conversation, checklist):
    payload["criteria"][3]["checked_range"] = {"from": "m2", "to": "m10"}
    assert validate_response(payload, conversation, checklist) == []


def test_found_action_needs_evidence(payload, conversation, checklist):
    payload["criteria"][0]["evidence"] = []
    assert "EVIDENCE_REQUIRED" in codes(payload, conversation, checklist, "contact")


def test_na_needs_evidence_and_condition_reference(payload, conversation, checklist):
    payload["criteria"][1].update(reasoning="Не применяется.", evidence=[])
    assert {"EVIDENCE_REQUIRED", "NA_CONDITION_REFERENCE_REQUIRED"} <= codes(payload, conversation, checklist, "identification")


@pytest.mark.parametrize("prefix", ["Condition met: ", "Орындалған шарт: ", "Выполнено условие: "])
def test_na_can_explain_in_different_language_and_quote_condition(payload, conversation, checklist, prefix):
    payload["criteria"][1]["reasoning"] = prefix + '"Запрос не требовал идентификации."'
    assert validate_response(payload, conversation, checklist) == []


def test_missing_and_duplicate_criteria(payload, conversation, checklist):
    payload["criteria"][1] = deepcopy(payload["criteria"][0])
    errors = validate_response(payload, conversation, checklist)
    assert any(e["criterion_id"] == "contact" and e["code"] == "DUPLICATE_CRITERION" for e in errors)
    assert any(e["criterion_id"] == "identification" and e["code"] == "MISSING_CRITERION" for e in errors)


@pytest.mark.parametrize("cid", ["unexpected", "inactive"])
def test_extra_or_inactive_criterion(payload, conversation, checklist, cid):
    extra = deepcopy(payload["criteria"][0])
    extra["criterion_id"] = cid
    payload["criteria"].append(extra)
    assert "EXTRA_CRITERION" in codes(payload, conversation, checklist, cid)


def test_conversation_id_must_match(payload, conversation, checklist):
    payload["conversation_id"] = "other"
    assert "CONVERSATION_ID_MISMATCH" in codes(payload, conversation, checklist)


@pytest.mark.parametrize("value", [None, "", [], {}])
def test_reasoning_required(payload, conversation, checklist, value):
    payload["criteria"][0]["reasoning"] = value
    assert "REASONING_REQUIRED" in codes(payload, conversation, checklist, "contact")


@pytest.mark.parametrize("value", [None, {}, "evidence"])
def test_evidence_must_be_array(payload, conversation, checklist, value):
    payload["criteria"][0]["evidence"] = value
    assert "EVIDENCE_NOT_LIST" in codes(payload, conversation, checklist, "contact")


@pytest.mark.parametrize("value", [None, [], "bad", {"criterion_id": []}, {"criterion_id": 1}])
def test_malformed_criterion_never_crashes(payload, conversation, checklist, value):
    payload["criteria"][0] = value
    assert "INVALID_CRITERION" in codes(payload, conversation, checklist)


@pytest.mark.parametrize("value", [None, {}, "bad"])
def test_malformed_criteria_container(payload, conversation, checklist, value):
    payload["criteria"] = value
    assert "CRITERIA_NOT_LIST" in codes(payload, conversation, checklist)


def test_enrichment_preserves_evidence_and_roles(payload, conversation, checklist):
    payload["criteria"][1].update(answer="yes", reasoning="Подтверждено системой.", evidence=[{"message_id": "m3", "quote": "Проверка личности пройдена."}])
    assert validate_response(payload, conversation, checklist) == []
    before = deepcopy(payload)
    enriched = enrich_criteria(payload, conversation)
    assert [r["evidence"][0]["speaker"] for r in enriched] == ["operator", "system", "operator", "bot"]
    assert payload == before


@pytest.mark.parametrize("text", ['{"criteria": []}', '```json\n{"criteria": []}\n```', '```\n{"criteria": []}\n```', ' \n```JSON\r\n{"criteria": []}\r\n```\n'])
def test_parse_plain_and_fenced_json(text):
    assert parse_model_json(text) == {"criteria": []}


@pytest.mark.parametrize("text", [
    "", "hello", "[]", "null", "42", "{} trailing", 'prefix {"x":1}',
    '```python\n{"x":1}\n```', '```json\n{}', '{}\n{}',
    '{"x":1,"x":2}', '{"nested":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}',
    '{"criteria":[',
])
def test_bad_json_is_not_repaired_or_silently_extracted(text):
    with pytest.raises(ModelJSONError):
        parse_model_json(text)
