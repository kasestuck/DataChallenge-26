from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from backend.config import DataSettings
from backend.dataset import (
    DatasetError,
    fetch_dataset,
    get_conversation,
    import_dataset,
    load_dataset,
    normalize_conversation,
    parse_dataset,
)
from backend.db import SCHEMA, connect_db


def conversation(cid="chat-1", *, channel="agent chat", timestamps=None):
    roles = ["client", "bot", "system", "operator"]
    timestamps = timestamps or ["00:02", "00:05", "00:07", "00:10"]
    return {
        "conversation_id": cid,
        "channel": channel,
        "language": "RU",
        "transcript": [
            {"timestamp": timestamp, "speaker": roles[index % len(roles)], "text": f"Реплика {index + 1}"}
            for index, timestamp in enumerate(timestamps)
        ],
        "audio_url": "https://audio.example/recording.wav" if channel == "voice call" else None,
    }


def envelope(*records, group="DEV"):
    return {
        "error": "", "isSuccess": True,
        "data": {"dataset_group_id": group, "total_records": len(records), "records": list(records)},
    }


@pytest.fixture
def conn():
    database = connect_db(":memory:")
    database.executescript(SCHEMA)
    try:
        yield database
    finally:
        database.close()


def settings(*, limit=1024 * 1024, api_url=None):
    return DataSettings(
        Path("unused-test.db"),
        api_url if api_url is not None else "https://dataset.example/invoke?api-version=1&sp=%2Ftriggers%2Frun&sig=test-signed-secret&group_id=DEV",
        "DEV", limit,
    )


def snapshot(conn):
    return {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("conversations", "messages")
    }


def test_chat_preserves_timestamps_text_roles_and_message_order():
    record = conversation()
    record["transcript"][0]["text"] = "  Не менять\nпробелы и регистр!  "
    original = deepcopy(record)
    result = normalize_conversation(record)
    assert [m["message_id"] for m in result["messages"]] == ["m1", "m2", "m3", "m4"]
    assert [m["position"] for m in result["messages"]] == [1, 2, 3, 4]
    assert [m["speaker"] for m in result["messages"]] == ["client", "bot", "system", "operator"]
    assert [m["timestamp"] for m in result["messages"]] == [m["timestamp"] for m in record["transcript"]]
    assert all(m["offset_sec"] is None for m in result["messages"])
    assert result["messages"][0]["text"] == record["transcript"][0]["text"]
    assert record == original
    assert json.loads(result["raw_json"]) == original
    assert result["warnings"] == []


def test_voice_offsets_respect_timezones_z_suffix_and_fractional_seconds():
    timestamps = [
        "2026-11-07T13:00:00+05:00",
        "2026-11-07T08:00:01.500Z",
        "2026-11-07T03:00:02-05:00",
    ]
    result = normalize_conversation(conversation(channel="voice call", timestamps=timestamps))
    assert [m["offset_sec"] for m in result["messages"]] == [0.0, 1.5, 2.0]
    assert [m["timestamp"] for m in result["messages"]] == timestamps
    assert result["audio_url"] == "https://audio.example/recording.wav"


@pytest.mark.parametrize("invalid", ["bad ISO", "2026-11-07T13:00:00", "2026-11-07", ""])
def test_invalid_or_timezone_less_timestamp_becomes_null(invalid):
    timestamps = ["2026-11-07T13:00:00+05:00", invalid, "2026-11-07T13:00:10+05:00"]
    result = normalize_conversation(conversation(channel="voice call", timestamps=timestamps))
    assert [m["offset_sec"] for m in result["messages"]] == [0.0, None, 10.0]
    assert result["messages"][1]["timestamp"] == invalid
    assert result["warnings"] == [{"conversation_id": "chat-1", "message_id": "m2", "code": "INVALID_ISO_TIMESTAMP"}]


def test_invalid_first_voice_timestamp_does_not_change_time_origin():
    result = normalize_conversation(conversation(channel="voice call", timestamps=[
        "not-a-date", "2026-11-07T13:00:00+05:00", "2026-11-07T13:00:10+05:00",
    ]))
    assert [m["offset_sec"] for m in result["messages"]] == [None, None, None]
    assert [w["code"] for w in result["warnings"]] == ["INVALID_ISO_TIMESTAMP", "INVALID_TIME_ORIGIN", "INVALID_TIME_ORIGIN"]


def test_upsert_is_idempotent_and_changed_transcript_replaces_messages(conn):
    record = conversation()
    first = import_dataset(conn, envelope(record))
    assert (first["inserted"], first["updated"], first["unchanged"]) == (1, 0, 0)
    before = snapshot(conn)
    again = import_dataset(conn, envelope(record))
    assert (again["inserted"], again["updated"], again["unchanged"]) == (0, 0, 1)
    assert snapshot(conn) == before

    changed = deepcopy(record)
    changed["language"] = "KZ"
    changed["transcript"] = changed["transcript"][:2]
    changed["transcript"][1]["text"] = "Жаңа жауап"
    updated = import_dataset(conn, envelope(changed))
    assert (updated["inserted"], updated["updated"], updated["unchanged"]) == (0, 1, 0)
    stored = get_conversation(conn, "chat-1")
    assert stored["revision"] == 2
    assert stored["language"] == "KZ"
    assert [m["message_id"] for m in stored["transcript"]] == ["m1", "m2"]
    assert stored["transcript"][1]["text"] == "Жаңа жауап"
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert stored["created_at"] == before["conversations"][0]["created_at"]
    assert "raw_json" not in stored
    assert get_conversation(conn, "does-not-exist") is None


def test_same_content_in_new_group_updates_group_and_revision(conn):
    import_dataset(conn, envelope(conversation()))
    result = import_dataset(conn, envelope(conversation(), group="CONTROL"), expected_group_id="CONTROL")
    assert result["updated"] == 1
    stored = get_conversation(conn, "chat-1")
    assert stored["dataset_group_id"] == "CONTROL"
    assert stored["revision"] == 2


def test_entire_payload_validated_before_writes(conn):
    import_dataset(conn, envelope(conversation("existing")))
    before = snapshot(conn)
    update = conversation("existing")
    update["transcript"][0]["text"] = "should not be saved"
    invalid = conversation("invalid")
    invalid["transcript"][-1]["speaker"] = "admin"
    with pytest.raises(DatasetError, match="speaker"):
        import_dataset(conn, envelope(update, conversation("new"), invalid))
    assert snapshot(conn) == before
    assert not conn.in_transaction


def test_database_failure_rolls_back_updates_deletes_and_inserts(conn):
    import_dataset(conn, envelope(conversation("existing")))
    before = snapshot(conn)
    conn.execute("""CREATE TRIGGER reject_test_message BEFORE INSERT ON messages
        WHEN NEW.text='TRIGGER_FAILURE' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
    update = conversation("existing")
    update["transcript"] = update["transcript"][:1]
    update["transcript"][0]["text"] = "changed but rolled back"
    second = conversation("new")
    second["transcript"][-1]["text"] = "TRIGGER_FAILURE"
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        import_dataset(conn, envelope(update, second))
    assert snapshot(conn) == before
    assert not conn.in_transaction


def test_import_failure_preserves_caller_transaction_and_earlier_writes(conn):
    conn.execute("""CREATE TRIGGER reject_test_message BEFORE INSERT ON messages
        WHEN NEW.text='TRIGGER_FAILURE' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO app_settings(key,value) VALUES ('caller-setting','keep-me')")
    failed = conversation("failing")
    failed["transcript"][-1]["text"] = "TRIGGER_FAILURE"
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        import_dataset(conn, envelope(conversation("also-rolled-back"), failed))
    assert conn.in_transaction
    assert conn.execute("SELECT value FROM app_settings WHERE key='caller-setting'").fetchone()[0] == "keep-me"
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM app_settings").fetchone()[0] == 0


def test_successful_import_does_not_commit_caller_transaction(conn):
    conn.execute("BEGIN")
    result = import_dataset(conn, envelope(conversation()))
    assert result["inserted"] == 1
    assert conn.in_transaction
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_api_failure_preserves_organizer_error_text():
    with pytest.raises(DatasetError) as failure:
        parse_dataset({"isSuccess": False, "error": "Набор ещё не открыт"})
    assert failure.value.code == "DATASET_API_ERROR"
    assert str(failure.value) == "Набор ещё не открыт"


@pytest.mark.parametrize("count", [0, 2, True, "1"])
def test_declared_record_count_must_be_exact_integer(count):
    payload = envelope(conversation())
    payload["data"]["total_records"] = count
    with pytest.raises(DatasetError) as failure:
        parse_dataset(payload)
    assert failure.value.code == "DATASET_INVALID"


def test_group_mismatch_is_not_imported(conn):
    with pytest.raises(DatasetError) as failure:
        import_dataset(conn, envelope(conversation(), group="OTHER"), expected_group_id="DEV")
    assert failure.value.code == "DATASET_GROUP_MISMATCH"
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_duplicate_conversation_ids_rejected(conn):
    with pytest.raises(DatasetError) as failure:
        import_dataset(conn, envelope(conversation(), conversation()))
    assert failure.value.code == "DATASET_DUPLICATE_ID"
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_fetch_and_load_preserve_signed_parameters_and_replace_group(conn):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=envelope(conversation(), group="CONTROL"))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = load_dataset(conn, settings(), "CONTROL", client=client)
        assert not client.is_closed  # A caller-owned client remains caller-owned.
    assert result["inserted"] == 1
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/invoke"
    assert request.url.params.get_list("group_id") == ["CONTROL"]
    assert request.url.params["sig"] == "test-signed-secret"
    assert request.url.params["sp"] == "/triggers/run"
    assert request.url.params["api-version"] == "1"


@pytest.mark.parametrize("content", [b"<html>not JSON</html>", b'{"unterminated"', b"\xff\xfe\xff"])
def test_invalid_json_is_explicit_failure(content):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(settings(), client=client)
    assert failure.value.code == "DATASET_INVALID_JSON"


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500, 503])
def test_non_success_http_does_not_expose_response_body(status):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text="test-signed-secret"))) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(settings(), client=client)
    assert failure.value.code == "DATASET_HTTP_ERROR"
    assert str(status) in str(failure.value)
    assert "test-signed-secret" not in str(failure.value)


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "DATASET_TIMEOUT"),
    (httpx.ConnectError, "DATASET_CONNECTION_ERROR"),
])
def test_network_failures_have_safe_messages(exception, code):
    def handle(request):
        raise exception(f"secret URL: {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(settings(), client=client)
    assert failure.value.code == code
    assert "test-signed-secret" not in str(failure.value)
    assert "https://" not in str(failure.value)


def test_byte_limit_rejects_complete_payload_without_partial_import(conn):
    content = json.dumps(envelope(conversation()), ensure_ascii=False).encode("utf-8")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        with pytest.raises(DatasetError) as failure:
            load_dataset(conn, settings(limit=len(content) - 1), client=client)
    assert failure.value.code == "DATASET_TOO_LARGE"
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_exact_byte_limit_is_allowed():
    payload = envelope(conversation())
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        assert fetch_dataset(settings(limit=len(content)), client=client) == payload


def test_organizer_error_redacts_signed_url_and_secret_values():
    def handle(request):
        return httpx.Response(200, json={
            "isSuccess": False,
            "error": f"Набор закрыт. URL={request.url}; sig=test-signed-secret",
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(settings(), client=client)
    assert failure.value.code == "DATASET_API_ERROR"
    assert "Набор закрыт" in str(failure.value)
    assert "test-signed-secret" not in str(failure.value)
    assert "https://dataset.example" not in str(failure.value)


def test_encoded_signature_echo_is_redacted():
    encoded_secret = "token%2Bwith%2Fspecial%3Dcharacters"
    decoded_secret = "token+with/special=characters"
    error = f"Неверная подпись: {encoded_secret}; decoded: {decoded_secret}"
    config = settings(api_url=f"https://dataset.example/invoke?sig={encoded_secret}")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"isSuccess": False, "error": error}))) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(config, client=client)
    assert failure.value.code == "DATASET_API_ERROR"
    assert encoded_secret not in str(failure.value)
    assert decoded_secret not in str(failure.value)


@pytest.mark.parametrize("url,code", [
    ("", "DATASET_NOT_CONFIGURED"),
    ("ftp://dataset.example/file", "DATASET_INVALID_URL"),
    ("https://user:password@dataset.example/invoke", "DATASET_INVALID_URL"),
])
def test_bad_configuration_never_sends_request(url, code):
    def fail_if_called(request):
        pytest.fail("A request must not be sent for invalid configuration")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        with pytest.raises(DatasetError) as failure:
            fetch_dataset(settings(api_url=url), client=client)
    assert failure.value.code == code
