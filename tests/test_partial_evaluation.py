"""Partial evaluation uses a real SQLite database and a model double only here."""

import copy
import json
from decimal import Decimal

import pytest

from backend import llm_client
from backend.checklist import diff_checklists, get_checklist, save_checklist
from backend.config import LLMOptions, MetaSettings
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.evaluator import EvaluationInputError, evaluate_conversation, get_evaluation
from backend.llm_client import LLMClient
from backend.meta_client import ModelRequestError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "partial.sqlite3"
    init_db(path)
    conn = connect_db(path)
    envelope = {"error": "", "isSuccess": True, "data": {
        "dataset_group_id": "DEV", "total_records": 1, "records": [{
            "conversation_id": "d1", "language": "RU", "channel": "agent chat", "audio_url": None,
            "transcript": [
                {"timestamp": "00:00", "speaker": "client", "text": "Здравствуйте, какие тарифы?"},
                {"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте, я Анна. Тарифы указаны на сайте."},
                {"timestamp": "00:02", "speaker": "operator", "text": "До свидания."},
            ],
        }],
    }}
    import_dataset(conn, envelope)
    requests = []
    replies = []

    def transport(system, user, **kwargs):
        assert not conn.in_transaction
        requests.append(user)
        answer = replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"content": answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False),
                "finish_reason": "stop", "model": "test", "http_status": 200,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    monkeypatch.setattr(llm_client, "request_completion", transport)
    client = LLMClient(conn, MetaSettings("test", "https://test.example/v1", "test"), LLMOptions())
    yield conn, client, requests, replies, envelope
    conn.close()


def response(checklist, ids=None):
    rows = []
    for c in checklist["criteria"]:
        if not c.get("active", True) or (ids is not None and c["criterion_id"] not in ids):
            continue
        answer = "no" if c["type"] == "flag" else (
            max(c["scale"], key=Decimal) if c["type"] == "scale" else "yes")
        rows.append({"criterion_id": c["criterion_id"], "answer": answer, "reasoning": "Действие есть в реплике.",
                     "evidence": [] if answer == "no" else [{"message_id": "m2", "quote": "Здравствуйте, я Анна."}],
                     "checked_range": {"from": "m1", "to": "m3"} if answer == "no" else None})
    return {"conversation_id": "d1", "criteria": rows}


def baseline(setup):
    conn, client, requests, replies, _ = setup
    checklist = get_checklist(conn)
    replies.append(response(checklist))
    result = evaluate_conversation(conn, "d1", client=client)
    assert result["status"] == "completed"
    requests.clear()
    return result, checklist


def test_only_changed_subset_is_sent_and_other_answers_reused(setup):
    conn, client, requests, replies, _ = setup
    previous, checklist = baseline(setup)
    checklist["criteria"][4]["description"] = "Новая формулировка эмпатии."
    new_version = save_checklist(conn, checklist)
    partial = response(new_version, ["05"])
    partial["criteria"][0].update(answer="no", evidence=[], checked_range={"from": "m1", "to": "m3"})
    replies.append(partial)
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "completed"
    assert result["total"] == "85.00"
    assert result["original"]["total"] == result["final"]["total"] == "85.00"
    assert len(requests) == 1
    assert '"criterion_id": "05"' in requests[0]
    assert '"criterion_id": "01"' not in requests[0]
    assert result["reevaluation"]["baseline_evaluation_id"] == previous["evaluation_id"]
    assert result["reevaluation"]["evaluated_criterion_ids"] == ["05"]
    assert result["reevaluation"]["reused_criterion_ids"] == ["01", "02", "03", "04", "06", "07", "08"]
    assert result["criteria"][0]["source"] == "reused"
    assert result["criteria"][0]["reasoning"] == previous["criteria"][0]["reasoning"]
    assert len(result["raw_responses"]) == 1
    assert result["usage"]["total_tokens"] == 30


@pytest.mark.parametrize("requested", [[], ["01"]])
def test_weight_change_only_recalculates_without_model_or_key(setup, monkeypatch, requested):
    conn, _, requests, _, _ = setup
    original, checklist = baseline(setup)
    checklist["criteria"][0]["weight"] = 50
    save_checklist(conn, checklist)

    def forbidden(*args, **kwargs):
        pytest.fail("Pure recalculation must not instantiate an LLM client")

    monkeypatch.setattr("backend.evaluator.LLMClient", forbidden)
    result = evaluate_conversation(conn, "d1", criterion_ids=requested)
    assert requests == []
    assert result["status"] == "completed"
    assert result["reevaluation"]["mode"] == "recalculation"
    assert result["total"] == "100.00"
    assert result["criteria"][0]["effective_weight"] == "35.71"
    assert result["usage"]["calls"] == result["usage"]["total_tokens"] == 0
    assert result["usage"]["cost"] == "0"
    assert result["final"]["usage"]["total_tokens"] == 0
    assert get_evaluation(conn, original["evaluation_id"])["total"] == original["total"]


def test_no_baseline_evaluates_all_even_with_subset(setup):
    conn, client, requests, replies, _ = setup
    checklist = get_checklist(conn)
    replies.append(response(checklist))
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "completed"
    assert len(result["reevaluation"]["evaluated_criterion_ids"]) == 8
    assert result["reevaluation"]["fallback_reason"] == "no_previous_valid_evaluation_for_transcript_revision"
    assert '"criterion_id": "01"' in requests[0]


def test_new_revision_cannot_reuse_previous_snapshot(setup):
    conn, client, requests, replies, envelope = setup
    _, checklist = baseline(setup)
    save_checklist(conn, checklist)
    changed = copy.deepcopy(envelope)
    changed["data"]["records"][0]["transcript"][0]["text"] += " Новый вопрос."
    import_dataset(conn, changed)
    replies.append(response(checklist))
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "completed"
    assert result["reevaluation"]["mode"] == "full"
    assert result["reevaluation"]["baseline_evaluation_id"] is None


def test_semantically_changed_and_added_criteria_automatically_included(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    checklist["criteria"][0]["yes_text"] += " Новое условие."
    added = copy.deepcopy(checklist["criteria"][1])
    added.update(criterion_id="new", name="Новый критерий")
    checklist["criteria"].append(added)
    new_version = save_checklist(conn, checklist)
    replies.append(response(new_version, ["01", "05", "new"]))
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "completed"
    assert result["reevaluation"]["evaluated_criterion_ids"] == ["01", "05", "new"]
    assert '"criterion_id": "02"' not in requests[0]


def test_removed_criterion_is_excluded_without_call(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    checklist["criteria"][0]["active"] = False
    save_checklist(conn, checklist)
    result = evaluate_conversation(conn, "d1", criterion_ids=[], client=client)
    assert result["status"] == "completed"
    assert requests == []
    assert "01" not in {c["criterion_id"] for c in result["criteria"]}


@pytest.mark.parametrize("bad", ["bad JSON", {"conversation_id": "other", "criteria": []}])
def test_invalid_partial_response_keeps_reused_answers_but_no_total(setup, bad):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    checklist["criteria"][4]["description"] = "Changed"
    save_checklist(conn, checklist)
    replies.extend([bad, bad])
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "needs_review"
    assert result["total"] is None
    assert result["criteria"][4]["answer"] is None
    assert result["criteria"][0]["answer"] == "yes"
    assert result["criteria"][0]["valid"] is True
    assert result["criteria"][0]["points"] is None
    assert result["usage"]["calls"] == 2


def test_partial_api_failure_keeps_reused_answers_and_error(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    save_checklist(conn, checklist)
    replies.append(ModelRequestError("MODEL_ACCESS_DENIED", "Access denied", 401))
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "failed"
    assert result["total"] is None
    assert result["criteria"][0]["answer"] == "yes"
    assert result["criteria"][4]["answer"] is None
    assert result["error"]["code"] == "MODEL_ACCESS_DENIED"


@pytest.mark.parametrize("ids", [["missing"], ["01", "01"], "01", [1]])
def test_invalid_subset_rejected_before_model(setup, ids):
    conn, client, requests, replies, _ = setup
    with pytest.raises(EvaluationInputError, match="criterion_ids") as error:
        evaluate_conversation(conn, "d1", criterion_ids=ids, client=client)
    assert error.value.code == "INVALID_CRITERION_IDS"
    assert requests == []


def test_explicit_full_evaluation_does_not_reuse_previous(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    save_checklist(conn, checklist)
    replies.append(response(checklist))
    result = evaluate_conversation(conn, "d1", client=client)
    assert result["reevaluation"]["mode"] == "full"
    assert result["reevaluation"]["baseline_evaluation_id"] is None
    assert len(requests) == 1


def test_final_reviewer_answer_reused_without_invented_evidence(setup):
    conn, client, requests, replies, _ = setup
    previous, checklist = baseline(setup)
    original_blob = conn.execute("SELECT original_result_json FROM evaluations").fetchone()[0]
    final = copy.deepcopy(previous["final"])
    final["criteria"][0].update(answer="no", source="reviewer", reasoning="Проверяющий изменил решение.",
                                evidence=[], checked_range=None)
    with conn:
        conn.execute("UPDATE evaluations SET final_result_json=? WHERE evaluation_id=?",
                     (json.dumps(final, ensure_ascii=False), previous["evaluation_id"]))
    checklist["criteria"][0]["weight"] = 50
    save_checklist(conn, checklist)
    result = evaluate_conversation(conn, "d1", criterion_ids=[], client=client)
    assert result["status"] == "completed"
    assert result["total"] == "64.29"
    assert result["criteria"][0]["answer"] == "no"
    assert result["criteria"][0]["source_kind"] == "reviewer"
    assert result["criteria"][0]["evidence"] == []
    assert conn.execute("SELECT original_result_json FROM evaluations WHERE evaluation_id=?",
                        (previous["evaluation_id"],)).fetchone()[0] == original_blob
    assert requests == []


def test_checklist_diff_distinguishes_semantics_and_arithmetic(setup):
    conn, *_ = setup
    before = get_checklist(conn)
    after = copy.deepcopy(before)
    after["criteria"][0]["weight"] = 999
    after["criteria"][1]["description"] += "New condition"
    after["criteria"][2]["active"] = False
    diff = diff_checklists(before, after)
    assert diff["evaluation_required"] == ["02"]
    assert diff["recalculation_only"] == ["01"]
    assert diff["removed"] == ["03"]
    assert diff["changed"] == ["02"]


def test_get_evaluation_usage_includes_later_appeal_calls(setup):
    conn, client, requests, replies, _ = setup
    previous, checklist = baseline(setup)
    replies.append(response(checklist, ["05"]))
    client.complete("system", "appeal data", evaluation_id=previous["evaluation_id"], purpose="appeal")
    detail = get_evaluation(conn, previous["evaluation_id"])
    assert detail["usage"]["calls"] == 2
    assert detail["usage"]["total_tokens"] == 60
    assert detail["original"] == previous["original"]
    assert detail["original"]["usage"]["calls"] == 1


def test_same_version_and_other_family_are_not_partial_baselines(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    replies.append(response(checklist))
    same = evaluate_conversation(conn, "d1", criterion_ids=[], client=client)
    assert same["reevaluation"]["mode"] == "full"
    other = save_checklist(conn, checklist, checklist_id="other-family")
    replies.append(response(other))
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["reevaluation"]["mode"] == "full"


def test_partial_model_cannot_overwrite_reused_answer_with_extra_criterion(setup):
    conn, client, requests, replies, _ = setup
    _, checklist = baseline(setup)
    new = save_checklist(conn, checklist)
    bad = response(new, ["01", "05"])
    bad["criteria"][0]["answer"] = "no"
    replies.extend([bad, bad])
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "needs_review"
    assert result["criteria"][0]["answer"] == "yes"
    assert result["criteria"][0]["valid"] is True
    assert result["total"] is None


def test_job_id_persisted_on_evaluation(setup):
    conn, client, requests, replies, _ = setup
    checklist = get_checklist(conn)
    with conn:
        conn.execute("INSERT INTO jobs(job_id,checklist_version_id,status,total,request_json,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     ("job1", checklist["checklist_version_id"], "running", 1, "{}", "2026-01-01", "2026-01-01"))
    replies.append(response(checklist))
    result = evaluate_conversation(conn, "d1", client=client, job_id="job1")
    assert conn.execute("SELECT job_id FROM evaluations WHERE evaluation_id=?",
                        (result["evaluation_id"],)).fetchone()[0] == "job1"


def test_layers_expose_open_appeals_and_preserve_original_after_review(setup):
    from backend.appeals import create_appeal, decide_appeal

    conn, _, _, _, _ = setup
    previous, _ = baseline(setup)
    appeal = create_appeal(conn, previous["evaluation_id"], "01", "Приветствие недостаточно.")
    detail = get_evaluation(conn, previous["evaluation_id"])
    assert detail["has_open_appeal"] is True
    assert detail["criteria"][0]["has_open_appeal"] is True
    assert detail["original"] == previous["original"]
    decide_appeal(conn, appeal["appeal_id"], "change", "no", "Проверено вручную, условие не выполнено.")
    after = get_evaluation(conn, previous["evaluation_id"])
    assert after["has_open_appeal"] is False
    assert after["original"] == previous["original"]
    assert after["original"]["total"] == "100.00"
    assert after["final"]["total"] == "90.00"
    assert after["criteria"][0]["decisions"][0]["final_answer"] == "no"
    assert after["decisions"][0]["before_total"] == "100.00"
    assert after["decisions"][0]["after_total"] == "90.00"


def test_all_inactive_checklist_returns_known_zero_calls(setup):
    conn, client, requests, replies, _ = setup
    checklist = get_checklist(conn)
    for criterion in checklist["criteria"]:
        criterion["active"] = False
    save_checklist(conn, checklist)
    result = evaluate_conversation(conn, "d1", client=client)
    assert result["status"] == "completed"
    assert result["total"] is None
    assert result["usage"]["total_tokens"] == 0
    assert result["criteria"] == []
    assert requests == []


def test_recovered_failure_exposes_database_error_without_mutating_original(setup):
    conn, _, _, _, _ = setup
    previous, _ = baseline(setup)
    with conn:
        conn.execute("UPDATE evaluations SET status='failed',error_code=?,error_message=? WHERE evaluation_id=?",
                     ("JOB_INTERRUPTED", "Задача прервана перезапуском сервера.", previous["evaluation_id"]))
    detail = get_evaluation(conn, previous["evaluation_id"])
    assert detail["status"] == "failed"
    assert detail["error"] == {"code": "JOB_INTERRUPTED", "message": "Задача прервана перезапуском сервера."}
    assert detail["original"] == previous["original"]
    assert detail["original"]["error"] is None


def test_malformed_partial_criterion_id_is_review_error_not_merge_crash(setup):
    conn, client, _, replies, _ = setup
    _, checklist = baseline(setup)
    new = save_checklist(conn, checklist)
    malformed = response(new, ["05"])
    malformed["criteria"][0]["criterion_id"] = ["05"]
    replies.extend([malformed, malformed])
    result = evaluate_conversation(conn, "d1", criterion_ids=["05"], client=client)
    assert result["status"] == "needs_review"
    assert result["total"] is None
    assert result["criteria"][4]["answer"] is None
    assert result["criteria"][0]["answer"] == "yes"
