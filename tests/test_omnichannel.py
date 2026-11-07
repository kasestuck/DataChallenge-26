"""Channel normalization and lossless migration from the two-channel schema."""
import json
from contextlib import closing

from backend.checklist import get_checklist, seed_checklist
from backend.dataset import get_conversation, import_dataset, normalize_conversation
from backend.db import SCHEMA, connect_db, init_db
from backend.channels import channel_profile, timestamp_offsets


def test_old_channel_constraint_migration_preserves_rows_references_and_versions(tmp_path):
    path = tmp_path / "old-channel-schema.sqlite3"
    old_schema = SCHEMA.replace("CHECK(length(trim(channel)) BETWEEN 1 AND 80)",
                                "CHECK(channel IN ('agent chat','voice call'))")
    with closing(connect_db(path)) as conn:
        conn.executescript(old_schema)
        seed_checklist(conn)
        version = get_checklist(conn)["checklist_version_id"]
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {
            "dataset_group_id": "TEST", "total_records": 1, "records": [{
                "conversation_id": "synthetic-preserved", "channel": "agent chat", "language": "RU",
                "transcript": [{"timestamp": "00:00", "speaker": "operator", "text": "Синтетическая реплика для миграции."}]}]}})
        conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,conversation_snapshot_json,status,created_at,updated_at) VALUES (?,?,?,?,?,'completed',?,?)",
                     ("synthetic-evaluation", "synthetic-preserved", version, 1, '{"keep":true}', "before", "before"))
        conn.commit()
        before = {name: [dict(r) for r in conn.execute("SELECT * FROM " + name)]
                  for name in ("conversations", "messages", "evaluations", "checklist_versions")}
    init_db(path)
    init_db(path)
    with closing(connect_db(path)) as conn:
        after = {name: [dict(r) for r in conn.execute("SELECT * FROM " + name)] for name in before}
        assert before == after
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("UPDATE conversations SET channel='email' WHERE conversation_id='synthetic-preserved'")
        conn.commit()
        assert get_conversation(conn, "synthetic-preserved")["channel"] == "email"
        assert get_checklist(conn)["checklist_version_id"] == version


def test_channel_adapter_fallback_keeps_opaque_time_and_missing_values():
    original = {"conversation_id": "synthetic-custom", "channel": "new support channel", "language": "EN",
                "transcript": [{"speaker": "client", "text": "Synthetic question."},
                               {"speaker": "operator", "timestamp": "yesterday afternoon", "text": "Synthetic answer."},
                               {"speaker": "system", "timestamp": None, "text": "Synthetic event."}]}
    normalized = normalize_conversation(original)
    assert json.loads(normalized["raw_json"]) == original
    assert [m["timestamp"] for m in normalized["messages"]] == ["", "yesterday afternoon", ""]
    assert [m["offset_sec"] for m in normalized["messages"]] == [None, None, None]
    assert normalized["warnings"] == []
    assert channel_profile("new support channel").evaluation_source == "transcript_only"


def test_voice_adapter_retains_timezone_offsets_and_invalid_timestamp_warnings():
    offsets, warnings = timestamp_offsets("voice call", ["2026-11-07T10:00:00+05:00", "2026-11-07T05:00:07Z", "unknown"])
    assert offsets == [0, 7, None]
    assert warnings == [{"message_id": "m3", "code": "INVALID_ISO_TIMESTAMP"}]
