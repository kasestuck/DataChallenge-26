"""Upgrade existing stage-1 records without replacing or reinterpreting them."""

import sqlite3

import pytest

from backend.checklist import get_checklist, seed_checklist
from backend.dataset import import_dataset
from backend.db import SCHEMA, connect_db, init_db, migrate_schema


ADDED_LINES = (
    "    raw_responses_json TEXT NOT NULL DEFAULT '[]',\n",
    "    validation_attempts_json TEXT NOT NULL DEFAULT '[]',\n",
    "    usage_json TEXT,\n",
    "    raw_response TEXT,\n",
    "    finish_reason TEXT,\n",
    "    response_format_used INTEGER CHECK(response_format_used IN (0,1)),\n",
)


@pytest.fixture
def old_db(tmp_path):
    # Reproduce the previous schema's columns while keeping all its FK constraints.
    schema = SCHEMA
    for line in ADDED_LINES:
        assert line in schema
        schema = schema.replace(line, "")
    # This name already existed on evaluation_criteria in stage 1; remove only
    # the new evaluations column (the first occurrence in schema order).
    schema = schema.replace("    validation_errors_json TEXT NOT NULL DEFAULT '[]',\n", "", 1)
    path = tmp_path / "old-stage1.db"
    conn = connect_db(path)
    conn.executescript(schema)
    seed_checklist(conn)
    version = get_checklist(conn)
    import_dataset(conn, {
        "error": "", "isSuccess": True,
        "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [{
            "conversation_id": "migration-dialogue", "channel": "agent chat", "language": "RU",
            "audio_url": None,
            "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Привет!"}],
        }]},
    })
    conn.execute(
        """INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,
        conversation_revision,conversation_snapshot_json,status,original_result_json,final_result_json,
        original_total,final_total,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("old-evaluation", "migration-dialogue", version["checklist_version_id"], 1,
         '{"preserved_snapshot": true}', "completed", '{"total":"75.00"}', '{"total":"80.00"}',
         "75.00", "80.00", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        """INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,
        prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,price_output_per_1m,cost,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("old-call", "old-evaluation", "evaluation", "test-model", 1, 200, 100,
         31, 174, 205, "1", "2", "0.000379", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    assert "raw_responses_json" not in {r[1] for r in conn.execute("PRAGMA table_info(evaluations)")}
    assert "raw_response" not in {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
    conn.close()
    return path, version["checklist_version_id"]


def test_upgrade_preserves_rows_and_sets_audit_defaults(old_db):
    path, version_id = old_db
    init_db(path)
    conn = connect_db(path)
    try:
        evaluation = dict(conn.execute("SELECT * FROM evaluations WHERE evaluation_id='old-evaluation'").fetchone())
        assert evaluation["checklist_version_id"] == version_id
        assert evaluation["conversation_snapshot_json"] == '{"preserved_snapshot": true}'
        assert evaluation["original_total"] == "75.00"
        assert evaluation["final_total"] == "80.00"
        assert evaluation["original_result_json"] == '{"total":"75.00"}'
        assert evaluation["final_result_json"] == '{"total":"80.00"}'
        assert evaluation["raw_responses_json"] == "[]"
        assert evaluation["validation_errors_json"] == "[]"
        assert evaluation["validation_attempts_json"] == "[]"
        assert evaluation["usage_json"] is None
        call = dict(conn.execute("SELECT * FROM llm_calls WHERE call_id='old-call'").fetchone())
        assert (call["prompt_tokens"], call["completion_tokens"], call["total_tokens"]) == (31, 174, 205)
        assert call["cost"] == "0.000379"
        assert call["raw_response"] is None
        assert call["finish_reason"] is None
        assert call["response_format_used"] is None
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert get_checklist(conn)["checklist_version_id"] == version_id
    finally:
        conn.close()


def test_migration_and_startup_can_repeat_without_new_seed(old_db):
    path, version_id = old_db
    init_db(path)
    init_db(path)
    conn = connect_db(path)
    try:
        migrate_schema(conn)
        migrate_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM checklist_versions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
        assert get_checklist(conn)["checklist_version_id"] == version_id
        conn.execute("UPDATE llm_calls SET raw_response=?,finish_reason=?,response_format_used=? WHERE call_id=?",
                     ('{"real":"body"}', "stop", 1, "old-call"))
        conn.commit()
        migrate_schema(conn)
        assert conn.execute("SELECT raw_response FROM llm_calls WHERE call_id='old-call'").fetchone()[0] == '{"real":"body"}'
    finally:
        conn.close()


def test_additive_migration_respects_caller_transaction(old_db):
    path, _ = old_db
    conn = connect_db(path)
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO app_settings(key,value) VALUES ('caller-owned','keep-uncommitted')")
        migrate_schema(conn)
        assert conn.in_transaction
        assert "raw_response" in {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        conn.rollback()
        assert "raw_response" not in {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        assert conn.execute("SELECT value FROM app_settings WHERE key='caller-owned'").fetchone() is None
    finally:
        conn.close()


def test_migrated_response_format_boolean_constraint(old_db):
    path, _ = old_db
    init_db(path)
    conn = connect_db(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE llm_calls SET response_format_used=2 WHERE call_id='old-call'")
        conn.rollback()
    finally:
        conn.close()
