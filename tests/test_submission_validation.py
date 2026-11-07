"""Independent submission audit; all transcripts and files here are synthetic."""
import copy
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.submission_validation import independent_score, inspect_submission, read_submission_file
from scripts import validate_submission


@pytest.fixture
def sample():
    values = {"contact": True, "discovery": None, "identification": None,
              "consultation": 20, "empathy": True, "dialogue": 10,
              "closure": True, "critical": False}
    records, results = [], []
    transcript = [
        {"timestamp": "00:00", "speaker": "client", "text": "Синтетический общий вопрос о возможностях сервиса."},
        {"timestamp": "00:01", "speaker": "bot", "text": "Синтетическое сообщение автоматического помощника."},
        {"timestamp": "00:02", "speaker": "operator", "text": "Синтетический ответ оператора на общий вопрос."},
        {"timestamp": "00:03", "speaker": "system", "text": "Синтетическое событие окончания диалога."},
    ]
    for number in range(10):
        cid = f"synthetic-{number}"
        records.append({"conversation_id": cid, "channel": "agent chat", "transcript": copy.deepcopy(transcript),
                        "normalized_transcript": [{**m, "message_id": f"m{i}"} for i, m in enumerate(transcript, 1)]})
        results.append({"conversation_id": cid, "status": "ok", "criteria": [
            {"criterion_id": code, "value": value, "rationale": "Синтетическое обоснование для теста.",
             "evidence_turns": [1, 2, 3, 4] if value is False else [1] if value is None else [3]}
            for code, value in values.items()], "score": {"final_score": 100.0, "critical_flag": False}})
    payload = {"schema_version": "1.0", "team_name": "Synthetic Team", "model": {"mode": "external_api", "name": "synthetic-model"}, "results": results}
    return payload, records


def violations(sample):
    payload, records = sample
    return inspect_submission(payload, split="prod", records=records, expected_models=["synthetic-model"])


def codes(sample):
    return {issue["code"] for issue in violations(sample)}


def test_complete_synthetic_submission_passes(sample):
    assert violations(sample) == []


@pytest.mark.parametrize("operation,expected", [
    ("extra_criterion", "UNKNOWN_CRITERION"), ("missing_criterion", "CRITERION_COVERAGE"),
    ("duplicate_criterion", "CRITERION_COVERAGE"), ("boolean_string", "CRITERION_VALUE"),
    ("boolean_integer", "CRITERION_VALUE"), ("contact_na", "CRITERION_VALUE"),
    ("fractional_scale", "CRITERION_VALUE"), ("scale_string", "CRITERION_VALUE"),
    ("scale_intermediate", "CRITERION_VALUE"), ("wrong_total", "FINAL_SCORE_MISMATCH"),
    ("score_string", "FINAL_SCORE_VALUE"), ("score_precision", "FINAL_SCORE_VALUE"),
    ("score_boolean", "FINAL_SCORE_VALUE"), ("score_nan", "FINAL_SCORE_VALUE"),
    ("turn_outside", "EVIDENCE_OUT_OF_RANGE"), ("turn_zero", "EVIDENCE_INTEGER"),
    ("turn_bool", "EVIDENCE_INTEGER"), ("turn_string", "EVIDENCE_INTEGER"),
    ("duplicate_turn", "EVIDENCE_DUPLICATE"), ("empty_na_evidence", "EVIDENCE_REQUIRED"),
    ("empty_no_evidence", "EVIDENCE_REQUIRED"), ("empty_rationale", "RATIONALE"),
    ("critical_mismatch", "CRITICAL_FLAG"), ("unknown_model", "MODEL_NAME_MISMATCH"),
    ("wrong_mode", "MODEL_MODE_MISMATCH"), ("duplicate_id", "DUPLICATE_CONVERSATION_ID"),
    ("unknown_id", "UNKNOWN_CONVERSATION_ID"), ("missing_record", "RESULT_COUNT"),
])
def test_required_violations(sample, operation, expected):
    payload, _ = sample
    result = payload["results"][0]
    first = result["criteria"][0]
    if operation == "extra_criterion": result["criteria"].append({**first, "criterion_id": "extra"})
    elif operation == "missing_criterion": result["criteria"].pop()
    elif operation == "duplicate_criterion": result["criteria"][-1] = copy.deepcopy(first)
    elif operation == "boolean_string": first["value"] = "true"
    elif operation == "boolean_integer": first["value"] = 1
    elif operation == "contact_na": first["value"] = None
    elif operation == "fractional_scale": result["criteria"][3]["value"] = 20.0
    elif operation == "scale_string": result["criteria"][3]["value"] = "20"
    elif operation == "scale_intermediate": result["criteria"][3]["value"] = 17
    elif operation == "wrong_total": result["score"]["final_score"] = 99.0
    elif operation == "score_string": result["score"]["final_score"] = "100.00"
    elif operation == "score_precision": result["score"]["final_score"] = 99.999
    elif operation == "score_boolean": result["score"]["final_score"] = True
    elif operation == "score_nan": result["score"]["final_score"] = float("nan")
    elif operation == "turn_outside": first["evidence_turns"] = [5]
    elif operation == "turn_zero": first["evidence_turns"] = [0]
    elif operation == "turn_bool": first["evidence_turns"] = [True]
    elif operation == "turn_string": first["evidence_turns"] = ["3"]
    elif operation == "duplicate_turn": first["evidence_turns"] = [3, 3]
    elif operation == "empty_na_evidence": result["criteria"][1]["evidence_turns"] = []
    elif operation == "empty_no_evidence": result["criteria"][-1]["evidence_turns"] = []
    elif operation == "empty_rationale": first["rationale"] = "  "
    elif operation == "critical_mismatch": result["score"]["critical_flag"] = True
    elif operation == "unknown_model": payload["model"]["name"] = "another-model"
    elif operation == "wrong_mode": payload["model"]["mode"] = "local"
    elif operation == "duplicate_id": payload["results"][1]["conversation_id"] = result["conversation_id"]
    elif operation == "unknown_id": result["conversation_id"] = "not-in-source"
    elif operation == "missing_record": payload["results"].pop()
    assert expected in codes(sample)


@pytest.mark.parametrize("target", ["root", "model", "record", "criterion", "score"])
def test_extra_fields_rejected_everywhere(sample, target):
    payload, _ = sample
    locations = {"root": payload, "model": payload["model"], "record": payload["results"][0],
                 "criterion": payload["results"][0]["criteria"][0], "score": payload["results"][0]["score"]}
    locations[target]["unexpected"] = "synthetic"
    assert "EXTRA_FIELDS" in codes(sample)


def test_all_independent_errors_are_reported(sample):
    result = sample[0]["results"][0]
    result["criteria"][0]["value"] = "true"
    result["criteria"][1]["evidence_turns"] = []
    result["score"]["final_score"] = "bad"
    assert {"CRITERION_VALUE", "EVIDENCE_REQUIRED", "FINAL_SCORE_VALUE"} <= codes(sample)


def test_no_operator_is_error_not_zero_score(sample):
    payload, records = sample
    for item in records[0]["transcript"]:
        if item["speaker"] == "operator": item["speaker"] = "bot"
    records[0]["normalized_transcript"][2]["speaker"] = "bot"
    payload["results"][0]["score"]["final_score"] = 0
    assert "NO_OPERATOR_MESSAGES" in codes(sample)
    payload["results"][0] = {"conversation_id": "synthetic-0", "status": "error", "criteria": [], "score": None,
                             "error": "В синтетическом примере отсутствуют реплики оператора."}
    assert violations(sample) == []


@pytest.mark.parametrize("reason", [None, "", "   ", 42])
def test_error_requires_reason(sample, reason):
    sample[0]["results"][0] = {"conversation_id": "synthetic-0", "status": "error", "criteria": [], "score": None, "error": reason}
    assert "ERROR_REASON" in codes(sample)


def test_error_cannot_hide_as_zero(sample):
    sample[0]["results"][0] = {"conversation_id": "synthetic-0", "status": "error", "criteria": [], "score": 0, "error": "Синтетический отказ API."}
    assert "ERROR_SCORE" in codes(sample)


def test_numbering_includes_client_bot_and_system(sample):
    assert violations(sample) == []
    sample[1][0]["normalized_transcript"][2]["message_id"] = "m1"
    assert "MESSAGE_NUMBERING" in codes(sample)


def test_source_channel_composition_checked(sample):
    sample[1][0]["channel"] = "voice call"
    assert "DATASET_COMPOSITION" in codes(sample)


def test_independent_arithmetic_raw_scales_na_and_flag():
    values = {"contact": True, "discovery": True, "identification": True, "consultation": 15,
              "empathy": True, "dialogue": 7, "closure": True, "critical": False}
    assert independent_score(values) == Decimal("90.50")
    values["critical"] = True
    assert independent_score(values) == Decimal("90.50")
    values["identification"] = None
    assert independent_score(values) == Decimal("88.13")


@pytest.mark.parametrize("text,expected", [
    ('{"results": [],}', "INVALID_JSON"), ('{/*comment*/"results": []}', "INVALID_JSON"),
    ('{"value": NaN}', "INVALID_JSON"), ('{"value": Infinity}', "INVALID_JSON"),
    ('{"results":[{"status":"ok","status":"error"}]}', "DUPLICATE_JSON_KEY"),
])
def test_strict_json_reader(tmp_path, text, expected):
    path = tmp_path / "submission.json"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    _, issues = read_submission_file(path)
    assert expected in {i["code"] for i in issues}
    assert path.read_bytes() == before


def test_reader_rejects_non_utf8(tmp_path):
    path = tmp_path / "submission.json"
    path.write_bytes(b'{"team_name":"\xff"}')
    assert read_submission_file(path)[1][0]["code"] == "INVALID_UTF8"


def test_api_missing_url_reports_not_checked_without_network(sample, tmp_path, monkeypatch, capsys):
    path = tmp_path / "submission.json"
    path.write_text(json.dumps(sample[0], ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(validate_submission, "load_data_settings", lambda: SimpleNamespace(api_url="", group_id="PROD", database_path=tmp_path / "absent.sqlite3"))
    def forbidden(*args, **kwargs):
        raise AssertionError("No network call is authorized without a URL")
    monkeypatch.setattr(validate_submission, "fetch_dataset", forbidden)
    assert validate_submission.main([str(path), "--split", "prod", "--dataset-source", "api", "--group-id", "PROD"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "НЕ ПРОВЕРЕНО"
    assert "DATASET_API_URL" in output["not_checked"][0]["message"]
    assert path.read_bytes() == before


def test_cli_reports_local_failures_even_without_reference(sample, tmp_path, monkeypatch, capsys):
    sample[0]["results"][0]["criteria"][0]["value"] = "true"
    path = tmp_path / "submission.json"
    path.write_text(json.dumps(sample[0]), encoding="utf-8")
    monkeypatch.setattr(validate_submission, "load_data_settings", lambda: SimpleNamespace(api_url="", group_id="PROD", database_path=tmp_path / "absent.sqlite3"))
    assert validate_submission.main([str(path), "--split", "prod", "--group-id", "PROD"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "FAIL" and output["not_checked"]
    assert "CRITERION_VALUE" in {i["code"] for i in output["violations"]}


def test_database_path_override_reads_isolated_reference_only(sample, tmp_path, monkeypatch, capsys):
    payload, records = sample
    submission = tmp_path / "results.json"
    submission.write_text(json.dumps(payload), encoding="utf-8")
    database = tmp_path / "rehearsal.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE marker (value INTEGER)")
    before = database.read_bytes()
    main_database = tmp_path / "main-does-not-exist.sqlite3"
    monkeypatch.setattr(validate_submission, "load_data_settings", lambda: SimpleNamespace(api_url="", group_id="DEV", database_path=main_database))
    def isolated_records(conn, group):
        assert group == "DEV_REHEARSAL"
        assert conn.execute("PRAGMA database_list").fetchone()[2] == str(database)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO marker VALUES (1)")
        return records
    monkeypatch.setattr(validate_submission, "_database_records", isolated_records)
    monkeypatch.setattr(validate_submission, "get_checklist", lambda *args: {"checklist_version_id": "synthetic-version"})
    monkeypatch.setattr(validate_submission, "_actual_models", lambda *args: (["synthetic-model"], []))
    assert validate_submission.main([str(submission), "--split", "prod", "--group-id", "DEV_REHEARSAL",
                                     "--database-path", str(database)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PASS" and report["group_id"] == "DEV_REHEARSAL"
    assert database.read_bytes() == before and not main_database.exists()


def test_cli_does_not_echo_key_embedded_in_invalid_field_name(sample, tmp_path, monkeypatch, capsys):
    secret = "unit-test-validator-secret"
    payload = sample[0]
    payload[secret] = "invalid extra field"
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(validate_submission, "read_environment", lambda: {"MODEL_API_KEY": secret})
    monkeypatch.setattr(validate_submission, "load_data_settings", lambda: SimpleNamespace(api_url="", group_id="PROD", database_path=tmp_path / "absent.sqlite3"))
    assert validate_submission.main([str(path), "--split", "prod", "--group-id", "PROD"]) == 1
    output = capsys.readouterr().out
    assert secret not in output and "[REDACTED]" in output
