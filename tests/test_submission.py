"""Submission contract tests use synthetic transcripts and persisted synthetic results."""
import copy
import json
from uuid import uuid4

import pytest

from backend.checklist import get_checklist, save_checklist
from backend.dataset import get_conversation, import_dataset
from backend.db import connect_db, init_db
from backend.scoring import calculate_scores
from backend.submission import (
    RULES, SubmissionError, build_submission, validate_group_records,
    validate_official_checklist, validate_submission,
)


def record(cid, channel="agent chat"):
    return {"conversation_id": cid, "channel": channel, "language": "EN", "transcript": [
        {"timestamp": "00:00", "speaker": "system", "text": "Synthetic session marker."},
        {"timestamp": "00:01", "speaker": "client", "text": "Synthetic example request."},
        {"timestamp": "00:02", "speaker": "bot", "text": "Synthetic queue message."},
        {"timestamp": "00:03", "speaker": "operator", "text": "Synthetic operator response."},
    ]}


def import_records(conn, group, records):
    import_dataset(conn, {"error": "", "isSuccess": True, "data": {
        "dataset_group_id": group, "total_records": len(records), "records": records}})


def store_evaluation(conn, cid, *, version=None, overrides=None, status="completed", model="synthetic-stored-model",
                     plan=None, mutate=None):
    config = get_checklist(conn, version)
    conversation = get_conversation(conn, cid)
    answers = {r[0]: (str(max(r[5])) if r[5] else "no" if r[2] == "flag" else "yes") for r in RULES}
    answers.update(overrides or {})
    scores = calculate_scores(config, answers)
    criteria = [{"criterion_id": cid, "answer": answer, "valid": True, "reasoning": "Synthetic stored explanation.",
                 "evidence": [{"message_id": "m4", "quote": "Synthetic operator response.", "speaker": "operator"}],
                 "checked_range": {"from": "m1", "to": "m4"} if answer == "no" else None}
                for cid, answer in answers.items()]
    result = {"criteria": criteria, "total": str(scores["total"]), "gross_violation": scores["gross_violation"],
              "reevaluation": plan or {"mode": "full", "baseline_evaluation_id": None, "reused_criterion_ids": []}}
    if mutate:
        mutate(result)
    eid = str(uuid4())
    conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,"
                 "conversation_snapshot_json,status,original_result_json,final_result_json,original_total,final_total,"
                 "gross_violation,prompt_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (eid, cid, config["checklist_version_id"], conversation["revision"], json.dumps(conversation), status,
                  json.dumps(result), json.dumps(result), result["total"], result["total"], int(result["gross_violation"]),
                  "a" * 64, "2026-01-01", "2026-01-01"))
    if model:
        conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,"
                     "prompt_tokens,completion_tokens,total_tokens,created_at) VALUES (?,?, 'evaluation',?,1,200,10,20,10,30,'2026-01-01')",
                     (str(uuid4()), eid, model))
    conn.commit()
    return eid


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "synthetic-submission.sqlite3"
    init_db(path)
    conn = connect_db(path)
    import_records(conn, "CONTROL_TEST", [record(f"synthetic-{i}") for i in range(10)])
    for i in range(10):
        store_evaluation(conn, f"synthetic-{i}")
    yield conn
    conn.close()


def build(conn, **kwargs):
    return build_submission(conn, group_id="CONTROL_TEST", split="prod", team_name="Synthetic Team", **kwargs)


def test_export_complete_group_is_read_only_and_uses_actual_stored_model(database, monkeypatch):
    monkeypatch.setenv("META_MODEL", "not-the-stored-model")
    before = database.total_changes
    payload, meta = build(database)
    assert database.total_changes == before
    assert payload["model"] == {"mode": "external_api", "name": "synthetic-stored-model"}
    assert meta["counts"] == {"ok": 10, "error": 0}
    assert len(meta["evaluation_ids"]) == 10
    assert payload["results"][0]["score"] == {"final_score": 100.0, "critical_flag": False}
    assert [c["criterion_id"] for c in payload["results"][0]["criteria"]] == [r[1] for r in RULES]
    assert validate_submission(payload, expected_ids=meta["expected_ids"], transcript_lengths=meta["transcript_lengths"])


def test_scale_is_raw_integer_na_is_null_and_critical_does_not_zero_score(database):
    store_evaluation(database, "synthetic-0", overrides={"02": "na", "03": "na", "04": "15", "06": "7", "08": "yes"})
    payload, _ = build(database)
    row = payload["results"][0]
    criteria = {c["criterion_id"]: c for c in row["criteria"]}
    assert criteria["discovery"]["value"] is None
    assert criteria["identification"]["value"] is None
    assert type(criteria["contact"]["value"]) is bool
    assert criteria["consultation"]["value"] == 15
    assert type(criteria["dialogue"]["value"]) is int
    assert criteria["dialogue"]["value"] == 7
    assert row["score"] == {"final_score": 85.38, "critical_flag": True}


def test_evidence_indices_include_bot_client_system_and_checked_range(database):
    def mutate(result):
        result["criteria"][0]["evidence"] = [{"message_id": "m1", "quote": "Synthetic session marker."},
                                             {"message_id": "m3", "quote": "Synthetic queue message."}]
        result["criteria"][0]["checked_range"] = {"from": "m2", "to": "m4"}
    store_evaluation(database, "synthetic-0", mutate=mutate)
    payload, _ = build(database)
    assert payload["results"][0]["criteria"][0]["evidence_turns"] == [1, 2, 3, 4]
    assert payload["results"][1]["criteria"][0]["evidence_turns"] == [4]


@pytest.mark.parametrize("status", ["failed", "needs_review", "pending", "running"])
def test_latest_failed_or_incomplete_attempt_is_not_replaced_with_older_success(database, status):
    latest = store_evaluation(database, "synthetic-0", status=status)
    payload, meta = build(database)
    row = payload["results"][0]
    assert row["status"] == "error" and row["criteria"] == [] and row["score"] is None and row["error"]
    assert meta["counts"] == {"ok": 9, "error": 1}
    assert latest in meta["evaluation_ids"]


def test_changed_transcript_revision_is_not_exported_as_current(database):
    database.execute("UPDATE conversations SET revision=revision+1 WHERE conversation_id='synthetic-0'")
    database.commit()
    payload, _ = build(database)
    assert payload["results"][0]["status"] == "error"
    assert payload["results"][0]["score"] is None


@pytest.mark.parametrize("mutation", [
    lambda r: r["criteria"][0].update(valid=False),
    lambda r: r["criteria"][0].update(answer="na"),
    lambda r: r["criteria"][0].update(reasoning=""),
    lambda r: r["criteria"][0]["evidence"][0].update(message_id="missing"),
    lambda r: r["criteria"][0]["evidence"][0].update(quote="A different synthetic statement."),
    lambda r: r["criteria"][0].update(checked_range={"from": "m4", "to": "m1"}),
    lambda r: r.update(total="99.00"),
    lambda r: r.update(gross_violation=True),
    lambda r: r["criteria"].append(copy.deepcopy(r["criteria"][0])),
])
def test_invalid_stored_data_becomes_explicit_error_not_zero(database, mutation):
    store_evaluation(database, "synthetic-0", mutate=mutation)
    payload, _ = build(database)
    assert payload["results"][0]["status"] == "error"
    assert payload["results"][0]["score"] is None


def test_same_revision_but_corrupted_snapshot_fails_export_record(database):
    eid = store_evaluation(database, "synthetic-0")
    snapshot = get_conversation(database, "synthetic-0")
    snapshot["transcript"][0]["text"] = "Different synthetic source."
    database.execute("UPDATE evaluations SET conversation_snapshot_json=? WHERE evaluation_id=?", (json.dumps(snapshot), eid))
    database.commit()
    payload, _ = build(database)
    assert payload["results"][0]["status"] == "error"


def test_explicit_version_selects_only_that_version(database):
    old = get_checklist(database)["checklist_version_id"]
    config = get_checklist(database)
    config["criteria"][0]["description"] = "Synthetic changed wording."
    new = save_checklist(database, config)
    store_evaluation(database, "synthetic-0", overrides={"01": "no"})
    current, meta = build(database)
    original, _ = build(database, checklist_version_id=old)
    assert meta["checklist_version_id"] == new["checklist_version_id"]
    assert meta["counts"] == {"ok": 1, "error": 9}
    assert current["results"][0]["criteria"][0]["value"] is False
    assert original["results"][0]["criteria"][0]["value"] is True


def test_partial_export_includes_models_of_reused_baseline(database):
    first = store_evaluation(database, "synthetic-0", model="synthetic-first")
    store_evaluation(database, "synthetic-0", model="synthetic-second", plan={
        "mode": "partial", "baseline_evaluation_id": first, "reused_criterion_ids": ["01"]})
    payload, _ = build(database)
    assert payload["model"]["name"] == "synthetic-first, synthetic-second, synthetic-stored-model"


def test_missing_model_provenance_is_never_taken_from_environment(database, monkeypatch):
    monkeypatch.setenv("META_MODEL", "synthetic-env-only")
    database.execute("DELETE FROM llm_calls")
    database.commit()
    with pytest.raises(SubmissionError, match="имя фактически"):
        build(database)


def test_completed_result_does_not_attribute_failed_model_attempt(database):
    eid = store_evaluation(database, "synthetic-0", model="synthetic-successful-model")
    database.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,error_code,duration_ms,"
                     "created_at) VALUES (?,?,'evaluation','synthetic-unavailable-model',1,404,'MODEL_NOT_FOUND',10,'2026-01-01')",
                     (str(uuid4()), eid))
    database.commit()
    payload, _ = build(database)
    assert "synthetic-unavailable-model" not in payload["model"]["name"]
    assert "synthetic-successful-model" in payload["model"]["name"]


def test_all_failed_group_retains_attempted_name_and_error_records(database):
    database.execute("UPDATE evaluations SET status='failed'")
    database.execute("UPDATE llm_calls SET http_status=401,error_code='MODEL_ACCESS_DENIED',"
                     "model='synthetic-attempted-model'")
    database.commit()
    payload, meta = build(database)
    assert payload["model"]["name"] == "synthetic-attempted-model"
    assert meta["counts"] == {"ok": 0, "error": 10}
    assert "попытки" in meta["model_name_basis"]
    assert all(r["score"] is None and r["criteria"] == [] for r in payload["results"])


def test_unofficial_checklist_rejected_before_export(database):
    config = get_checklist(database)
    config["criteria"][4]["criterion_id"] = "09"
    save_checklist(database, config)
    with pytest.raises(SubmissionError) as error:
        build(database)
    assert error.value.code == "SUBMISSION_CHECKLIST_MISMATCH"


@pytest.mark.parametrize("change", [
    lambda c: c["criteria"][0].update(weight=11),
    lambda c: c["criteria"][0].update(allow_na=True),
    lambda c: c["criteria"][5]["scale"].update({"8": "Synthetic extra step"}),
    lambda c: c["criteria"][7].update(scored=True),
])
def test_official_preflight_rejects_weights_nullability_scale_or_flag(database, change):
    config = get_checklist(database)
    change(config)
    with pytest.raises(SubmissionError):
        validate_official_checklist(config)


def test_group_preflight_and_fresh_ids_exclude_stale_records(database):
    import_records(database, "CONTROL_TEST", [record("synthetic-stale")])
    with pytest.raises(SubmissionError) as error:
        build(database)
    assert error.value.code == "SUBMISSION_COUNT_MISMATCH"
    payload, _ = build(database, conversation_ids=[f"synthetic-{i}" for i in range(10)])
    assert len(payload["results"]) == 10
    with pytest.raises(SubmissionError):
        build(database, conversation_ids=["synthetic-0"] * 10)
    with pytest.raises(SubmissionError):
        build(database, conversation_ids=[f"synthetic-{i}" for i in range(9)] + ["synthetic-unknown"])


def test_dev_requires_60_chats_5_calls_and_keeps_missing_evaluations(database):
    records = [record(f"synthetic-dev-{i}", "voice call" if i >= 60 else "agent chat") for i in range(65)]
    import_records(database, "DEV_TEST", records)
    store_evaluation(database, "synthetic-dev-0")
    payload, meta = build_submission(database, group_id="DEV_TEST", split="dev", team_name="Synthetic Team")
    assert len(payload["results"]) == 65 and meta["counts"] == {"ok": 1, "error": 64}
    records[-1]["channel"] = "agent chat"
    with pytest.raises(SubmissionError) as error:
        validate_group_records(records, "dev")
    assert error.value.code == "SUBMISSION_CHANNEL_MISMATCH"
    with pytest.raises(SubmissionError):
        validate_group_records(records[:10], "dev")
    records = [record(f"synthetic-c-{i}") for i in range(10)]
    records[-1]["channel"] = "voice call"
    with pytest.raises(SubmissionError):
        validate_group_records(records, "prod")


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=True),
    lambda p: p.update(team_name=""),
    lambda p: p["model"].update(mode="unknown"),
    lambda p: p["results"].append(copy.deepcopy(p["results"][0])),
    lambda p: p["results"][0]["criteria"][0].update(value=1),
    lambda p: p["results"][0]["criteria"][5].update(value=True),
    lambda p: p["results"][0]["criteria"][5].update(value=8),
    lambda p: p["results"][0]["criteria"][7].update(value=None),
    lambda p: p["results"][0]["criteria"][0].update(evidence_turns=[True]),
    lambda p: p["results"][0]["criteria"][0].update(evidence_turns=[0]),
    lambda p: p["results"][0]["criteria"][0].update(evidence_turns=[5]),
    lambda p: p["results"][0]["criteria"][0].update(evidence_turns=[4, 4]),
    lambda p: p["results"][0]["score"].update(final_score=True),
    lambda p: p["results"][0]["score"].update(final_score=float("nan")),
    lambda p: p["results"][0]["score"].update(final_score=99.0),
    lambda p: p["results"][0]["score"].update(critical_flag=True),
])
def test_strict_schema_rejects_bad_types_coverage_evidence_and_arithmetic(database, mutate):
    payload, meta = build(database)
    mutate(payload)
    with pytest.raises(SubmissionError):
        validate_submission(payload, expected_ids=meta["expected_ids"], transcript_lengths=meta["transcript_lengths"])


def test_error_contract_rejects_fake_zero_and_requires_reason(database):
    payload, meta = build(database)
    cid = payload["results"][0]["conversation_id"]
    payload["results"][0] = {"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                            "error": "Synthetic timeout."}
    assert validate_submission(payload, expected_ids=meta["expected_ids"], transcript_lengths=meta["transcript_lengths"])
    payload["results"][0]["score"] = 0
    with pytest.raises(SubmissionError):
        validate_submission(payload, expected_ids=meta["expected_ids"], transcript_lengths=meta["transcript_lengths"])


@pytest.mark.parametrize("answer", ["no", "na"])
def test_no_and_na_without_evidence_or_checked_range_export_as_error(database, answer):
    def clear(result):
        row = next(c for c in result["criteria"] if c["criterion_id"] == "02")
        row.update(evidence=[], checked_range=None)
    store_evaluation(database, "synthetic-0", overrides={"02": answer}, mutate=clear)
    payload, _ = build(database)
    result = payload["results"][0]
    assert result["status"] == "error" and result["score"] is None
    assert "Нет и N/A" in result["error"]


def test_legacy_no_operator_evaluation_cannot_be_exported_as_zero(database):
    original = record("synthetic-0")
    original["transcript"][3]["speaker"] = "bot"
    import_records(database, "CONTROL_TEST", [original])
    store_evaluation(database, "synthetic-0")
    payload, _ = build(database)
    result = payload["results"][0]
    assert result["status"] == "error" and result["score"] is None and result["criteria"] == []
    assert "отсутствуют реплики оператора" in result["error"]


@pytest.mark.parametrize("code,value", [("discovery", None), ("critical", False)])
def test_submission_file_requires_evidence_for_false_and_na(database, code, value):
    payload, metadata = build(database)
    row = next(c for c in payload["results"][0]["criteria"] if c["criterion_id"] == code)
    row.update(value=value, evidence_turns=[])
    with pytest.raises(SubmissionError, match="evidence_turns"):
        validate_submission(payload, expected_ids=metadata["expected_ids"], transcript_lengths=metadata["transcript_lengths"])
