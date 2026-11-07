import json
import sqlite3

import pytest

from backend import config
from backend.checklist import get_checklist, save_checklist
from backend.dataset import import_dataset
from backend.db import connect_db, init_db


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "storage" / "cqa.sqlite3"
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    connection = connect_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


def insert_conversation(connection):
    record = {
        "conversation_id": "db-test-dialog", "channel": "agent chat", "language": "RU",
        "transcript": [{"timestamp": "00:02", "speaker": "operator", "text": "Здравствуйте!"}],
        "audio_url": None,
    }
    import_dataset(connection, {
        "error": "", "isSuccess": True,
        "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [record]},
    })
    return record


def insert_evaluation(connection, version_id, conversation_id="db-test-dialog"):
    connection.execute(
        """INSERT INTO evaluations (
            evaluation_id,conversation_id,checklist_version_id,conversation_revision,
            conversation_snapshot_json,status,original_result_json,final_result_json,
            original_total,final_total,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("evaluation-test", conversation_id, version_id, 1, '{"transcript":[]}',
         "completed", '{"total":"50.00"}', '{"total":"50.00"}', "50.00", "50.00",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )


def test_required_tables_and_foreign_keys(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "conversations", "messages", "checklist_versions", "evaluations", "evaluation_criteria",
        "appeals", "decisions", "llm_calls", "jobs",
    } <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_initialization_never_requires_model_key(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Database initialization must not load model settings")

    monkeypatch.setattr(config, "load_settings", forbidden)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    path = tmp_path / "no-model.sqlite3"
    init_db(path)
    connection = connect_db(path)
    try:
        assert len(get_checklist(connection)["criteria"]) == 8
    finally:
        connection.close()


def test_reinitialization_preserves_conversation_and_active_version(conn, db_path):
    insert_conversation(conn)
    new = save_checklist(conn, get_checklist(conn))
    init_db(db_path)
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM checklist_versions").fetchone()[0] == 2
    assert get_checklist(conn)["checklist_version_id"] == new["checklist_version_id"]


def test_messages_cannot_reference_unknown_conversation(conn):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO messages(conversation_id,message_id,position,timestamp,speaker,text) VALUES (?,?,?,?,?,?)",
            ("missing", "m1", 1, "00:00", "client", "Question"),
        )
    conn.rollback()


@pytest.mark.parametrize("invalid_reference", ["conversation", "checklist"])
def test_evaluation_references_are_enforced(conn, invalid_reference):
    insert_conversation(conn)
    version_id = get_checklist(conn)["checklist_version_id"]
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert_evaluation(
            conn,
            "missing" if invalid_reference == "checklist" else version_id,
            "missing" if invalid_reference == "conversation" else "db-test-dialog",
        )
    conn.rollback()


def test_new_checklist_does_not_retarget_existing_evaluation(conn):
    insert_conversation(conn)
    old = get_checklist(conn)
    insert_evaluation(conn, old["checklist_version_id"])
    conn.commit()
    updated = get_checklist(conn)
    updated["criteria"][0]["weight"] = 25
    new = save_checklist(conn, updated)
    row = conn.execute("SELECT checklist_version_id FROM evaluations WHERE evaluation_id='evaluation-test'").fetchone()
    assert row[0] == old["checklist_version_id"]
    assert get_checklist(conn)["checklist_version_id"] == new["checklist_version_id"]
    assert get_checklist(conn, row[0])["criteria"][0]["weight"] == 10


def test_original_and_final_results_have_independent_storage(conn):
    insert_conversation(conn)
    version = get_checklist(conn)
    insert_evaluation(conn, version["checklist_version_id"])
    conn.execute(
        "INSERT INTO evaluation_criteria(evaluation_id,criterion_id,original_answer,final_answer) VALUES (?,?,?,?)",
        ("evaluation-test", "01", "no", "no"),
    )
    conn.execute(
        "UPDATE evaluations SET final_total=?,final_result_json=? WHERE evaluation_id=?",
        ("75.00", '{"total":"75.00"}', "evaluation-test"),
    )
    conn.execute(
        "UPDATE evaluation_criteria SET final_answer=? WHERE evaluation_id=? AND criterion_id=?",
        ("yes", "evaluation-test", "01"),
    )
    row = conn.execute("SELECT * FROM evaluations WHERE evaluation_id='evaluation-test'").fetchone()
    criterion = conn.execute("SELECT * FROM evaluation_criteria WHERE evaluation_id='evaluation-test'").fetchone()
    assert row["original_total"] == "50.00"
    assert json.loads(row["original_result_json"])["total"] == "50.00"
    assert row["final_total"] == "75.00"
    assert json.loads(row["final_result_json"])["total"] == "75.00"
    assert criterion["original_answer"] == "no"
    assert criterion["final_answer"] == "yes"


def test_appeal_requires_existing_evaluation_criterion(conn):
    insert_conversation(conn)
    insert_evaluation(conn, get_checklist(conn)["checklist_version_id"])
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO appeals(appeal_id,evaluation_id,criterion_id,comment,status,created_at) VALUES (?,?,?,?,?,?)",
            ("appeal-test", "evaluation-test", "missing", "Please review", "open", "2026-01-01T00:00:00Z"),
        )
    conn.rollback()
