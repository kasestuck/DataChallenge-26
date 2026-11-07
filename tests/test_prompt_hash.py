"""Prompt provenance uses file bytes; all conversations and model outputs here are synthetic."""

import hashlib
import json

import pytest

from backend import evaluator, llm_client
from backend.checklist import get_checklist, save_checklist, seed_checklist
from backend.config import LLMOptions, MetaSettings
from backend.dataset import import_dataset
from backend.db import SCHEMA, connect_db, init_db
from backend.llm_client import LLMClient
from backend.meta_client import ModelRequestError
from backend.models import EvaluationResponse


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "provenance.sqlite3"
    init_db(path)
    conn = connect_db(path)
    save_checklist(conn, {"name": "Synthetic rubric", "sections": [{"id": "s", "name": "Synthetic section"}],
                         "criteria": [{"criterion_id": "test", "name": "Acknowledgment", "section": "s",
                                       "type": "yes_no", "weight": 10, "allow_na": False,
                                       "yes_text": "An acknowledgment is present.", "no_text": "No acknowledgment.",
                                       "scored": True}]})
    import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1,
        "records": [{"conversation_id": "synthetic-hash", "channel": "agent chat", "language": "EN",
                     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "A synthetic request."},
                                    {"timestamp": "00:01", "speaker": "operator", "text": "Acknowledged."}]}]}})
    template = tmp_path / "prompt.md"
    template.write_bytes(b"Synthetic prompt\r\nOutput language: {output_language}\r\n")
    monkeypatch.setattr(evaluator, "PROMPT_PATH", template)
    requests, replies = [], []
    payload = {"conversation_id": "synthetic-hash", "criteria": [{"criterion_id": "test", "answer": "yes",
               "reasoning": "The synthetic action is recorded.", "evidence": [{"message_id": "m2", "quote": "Acknowledged."}],
               "checked_range": None}]}
    def request(system, user, **kwargs):
        requests.append(system)
        value = replies.pop(0) if replies else payload
        if isinstance(value, Exception):
            raise value
        return {"content": json.dumps(value), "finish_reason": "stop", "model": "synthetic-model", "http_status": 200,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(llm_client, "request_completion", request)
    client = LLMClient(conn, MetaSettings("synthetic", "https://synthetic.example/v1", "synthetic-model"), LLMOptions())
    yield conn, client, template, requests, replies
    conn.close()


def test_exact_file_hash_is_persisted_in_row_layers_and_api(setup):
    conn, client, template, requests, _ = setup
    source = template.read_bytes()
    result = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    expected = hashlib.sha256(source).hexdigest()
    assert result["prompt_hash"] == expected
    assert result["original"]["prompt_hash"] == result["final"]["prompt_hash"] == expected
    row = conn.execute("SELECT prompt_hash FROM evaluations WHERE evaluation_id=?", (result["evaluation_id"],)).fetchone()
    assert row[0] == expected
    assert requests == [source.decode("utf-8").replace("{output_language}", "русский")]
    assert EvaluationResponse.model_validate(result).prompt_hash == expected


def test_new_file_content_changes_only_new_evaluation_hash(setup):
    conn, client, template, _, _ = setup
    first = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    original = first["original"]
    template.write_bytes(template.read_bytes() + b"A new synthetic instruction.\n")
    second = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    assert second["prompt_hash"] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert second["prompt_hash"] != first["prompt_hash"]
    assert evaluator.get_evaluation(conn, first["evaluation_id"])["original"] == original


def test_file_edit_between_read_and_prompt_build_does_not_change_used_hash(setup, monkeypatch):
    conn, client, template, requests, _ = setup
    source = template.read_bytes()
    build = evaluator.build_prompts
    def concurrent_edit(*args, **kwargs):
        template.write_bytes(b"Changed after snapshot: {output_language}")
        return build(*args, **kwargs)
    monkeypatch.setattr(evaluator, "build_prompts", concurrent_edit)
    result = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    assert result["prompt_hash"] == hashlib.sha256(source).hexdigest()
    assert requests[0] == source.decode().replace("{output_language}", "русский")


def test_failed_model_call_keeps_prompt_hash(setup):
    conn, client, template, _, replies = setup
    replies.append(ModelRequestError("MODEL_ACCESS_DENIED", "Synthetic denied access.", 401))
    result = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    assert result["status"] == "failed"
    assert result["prompt_hash"] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert result["original"]["prompt_hash"] == result["prompt_hash"]


def test_needs_review_keeps_prompt_hash(setup):
    conn, client, template, _, replies = setup
    replies.extend([{}, {}])
    result = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    assert result["status"] == "needs_review"
    assert result["prompt_hash"] == hashlib.sha256(template.read_bytes()).hexdigest()


def test_recalculation_records_current_and_baseline_hash_without_model_key(setup, monkeypatch):
    conn, client, template, requests, _ = setup
    baseline = evaluator.evaluate_conversation(conn, "synthetic-hash", client=client)
    config = get_checklist(conn)
    config["criteria"][0]["weight"] = 25
    save_checklist(conn, config)
    template.write_bytes(b"Updated synthetic prompt {output_language}")
    def forbidden(*args, **kwargs):
        pytest.fail("Weight-only recalculation must not construct a model client")
    monkeypatch.setattr(evaluator, "LLMClient", forbidden)
    result = evaluator.evaluate_conversation(conn, "synthetic-hash", criterion_ids=[])
    assert result["status"] == "completed" and result["usage"]["calls"] == 0
    assert len(requests) == 1
    assert result["prompt_hash"] == hashlib.sha256(template.read_bytes()).hexdigest()
    assert result["reevaluation"]["baseline_prompt_hash"] == baseline["prompt_hash"]
    assert result["prompt_hash"] != result["reevaluation"]["baseline_prompt_hash"]


def test_old_schema_migration_leaves_legacy_hash_null_and_original_untouched(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = connect_db(path)
    conn.executescript(SCHEMA.replace("    prompt_hash TEXT,\n", ""))
    seed_checklist(conn)
    version = get_checklist(conn)
    import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1,
        "records": [{"conversation_id": "synthetic-legacy", "channel": "agent chat", "language": "EN",
                     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Synthetic legacy request."}]}]}})
    original = '{"status":"failed","error":{"code":"OLD_ERROR","message":"Synthetic failure."}}'
    conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,"
                 "conversation_snapshot_json,status,original_result_json,created_at,updated_at) "
                 "VALUES ('legacy','synthetic-legacy',?,1,'{}','failed',?,'old','old')", (version["checklist_version_id"], original))
    conn.commit()
    conn.close()
    init_db(path)
    init_db(path)
    conn = connect_db(path)
    try:
        row = conn.execute("SELECT prompt_hash,original_result_json FROM evaluations WHERE evaluation_id='legacy'").fetchone()
        assert row[0] is None and row[1] == original
        assert evaluator.get_evaluation(conn, "legacy")["prompt_hash"] is None
        conn.execute("UPDATE evaluations SET prompt_hash=? WHERE evaluation_id='legacy'", ("a" * 64,))
        conn.commit()
        init_db(path)
        assert conn.execute("SELECT prompt_hash FROM evaluations WHERE evaluation_id='legacy'").fetchone()[0] == "a" * 64
    finally:
        conn.close()
