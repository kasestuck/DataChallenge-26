"""Import organizer envelopes without evaluating or changing transcript timestamps."""

import hashlib
import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote_plus

import httpx

from backend.config import DataSettings
from backend.channels import timestamp_offsets
from backend.db import write_transaction


class DatasetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetError("DATASET_INVALID", f"Поле {field} должно быть непустой строкой.")
    return value


def normalize_conversation(record: dict) -> dict:
    if not isinstance(record, dict):
        raise DatasetError("DATASET_INVALID", "Каждая запись должна быть JSON-объектом.")
    cid = _required_string(record.get("conversation_id"), "conversation_id")
    channel = _required_string(record.get("channel"), "channel")
    language = record.get("language")
    if len(channel) > 80 or language not in ("RU", "KZ", "EN"):
        raise DatasetError("DATASET_INVALID", "Недопустимое значение channel или language.")
    transcript = record.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise DatasetError("DATASET_INVALID", "transcript должен быть непустым массивом.")
    audio_url = record.get("audio_url")
    if audio_url is not None and not isinstance(audio_url, str):
        raise DatasetError("DATASET_INVALID", "audio_url должен быть строкой или null.")
    messages = []
    warnings = []
    for index, item in enumerate(transcript, start=1):
        if not isinstance(item, dict):
            raise DatasetError("DATASET_INVALID", "Реплика должна быть JSON-объектом.")
        timestamp = item.get("timestamp")
        if timestamp is None:
            timestamp = ""
        text = item.get("text")
        speaker = item.get("speaker")
        if not isinstance(timestamp, str) or not isinstance(text, str):
            raise DatasetError("DATASET_INVALID", "timestamp и text должны быть строками.")
        if speaker not in ("client", "operator", "bot", "system"):
            raise DatasetError("DATASET_INVALID", "Недопустимая роль speaker.")
        messages.append({
            "message_id": f"m{index}", "position": index, "timestamp": timestamp,
            "speaker": speaker, "text": text, "offset_sec": None,
        })
    offsets, time_warnings = timestamp_offsets(channel, [m["timestamp"] for m in messages])
    for message, offset in zip(messages, offsets):
        message["offset_sec"] = offset
    warnings.extend({"conversation_id": cid, **warning} for warning in time_warnings)
    raw_json = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "conversation_id": cid, "channel": channel, "language": language,
        "audio_url": audio_url, "messages": messages, "warnings": warnings,
        "raw_json": raw_json, "content_hash": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
    }


def parse_dataset(payload: Any, *, expected_group_id: str | None = None) -> tuple[str, list[dict]]:
    if not isinstance(payload, dict):
        raise DatasetError("DATASET_INVALID", "Ответ должен быть JSON-объектом.")
    error = payload.get("error")
    if payload.get("isSuccess") is not True or error:
        message = error if isinstance(error, str) and error.strip() else "API организаторов вернул isSuccess=false."
        raise DatasetError("DATASET_API_ERROR", message)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DatasetError("DATASET_INVALID", "В ответе отсутствует объект data.")
    group = _required_string(data.get("dataset_group_id"), "dataset_group_id")
    if expected_group_id is not None and group != expected_group_id:
        raise DatasetError("DATASET_GROUP_MISMATCH", "API вернул другой dataset_group_id.")
    records = data.get("records")
    count = data.get("total_records")
    if not isinstance(records, list) or type(count) is not int or count != len(records):
        raise DatasetError("DATASET_INVALID", "total_records должен совпадать с длиной массива records.")
    normalized = [normalize_conversation(record) for record in records]
    ids = [record["conversation_id"] for record in normalized]
    if len(ids) != len(set(ids)):
        raise DatasetError("DATASET_DUPLICATE_ID", "В наборе повторяется conversation_id.")
    return group, normalized


def import_dataset(conn: sqlite3.Connection, payload: Any, *, expected_group_id: str | None = None) -> dict:
    group, records = parse_dataset(payload, expected_group_id=expected_group_id)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    now = datetime.now(timezone.utc).isoformat()
    # Validate the entire payload before any write; import is all-or-nothing.
    with write_transaction(conn):
        for record in records:
            cid = record["conversation_id"]
            existing = conn.execute(
                "SELECT content_hash,dataset_group_id FROM conversations WHERE conversation_id=?", (cid,)
            ).fetchone()
            if existing and existing["content_hash"] == record["content_hash"] and existing["dataset_group_id"] == group:
                counts["unchanged"] += 1
                continue
            if existing:
                counts["updated"] += 1
                conn.execute(
                    """UPDATE conversations SET dataset_group_id=?,channel=?,language=?,audio_url=?,
                    raw_json=?,content_hash=?,revision=revision+1,updated_at=? WHERE conversation_id=?""",
                    (group, record["channel"], record["language"], record["audio_url"], record["raw_json"],
                     record["content_hash"], now, cid),
                )
                conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
            else:
                counts["inserted"] += 1
                conn.execute(
                    """INSERT INTO conversations(conversation_id,dataset_group_id,channel,language,audio_url,
                    raw_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (cid, group, record["channel"], record["language"], record["audio_url"],
                     record["raw_json"], record["content_hash"], now, now),
                )
            conn.executemany(
                """INSERT INTO messages(conversation_id,message_id,position,timestamp,speaker,text,offset_sec)
                VALUES (?,?,?,?,?,?,?)""",
                [(cid, m["message_id"], m["position"], m["timestamp"], m["speaker"], m["text"], m["offset_sec"])
                 for m in record["messages"]],
            )
    return {"dataset_group_id": group, "total": len(records), **counts,
            "warnings": [warning for r in records for warning in r["warnings"]]}


def _safe_error(message: str, url: httpx.URL) -> str:
    # Organizer error text is part of the contract, but signed URLs must remain private.
    result = message.replace(str(url), "[REDACTED_URL]")
    secrets = set()
    for pair in url.query.decode().split("&"):
        key, _, value = pair.partition("=")
        if value and any(part in unquote_plus(key).lower() for part in ("sig", "key", "token", "secret", "authorization")):
            secrets.update((value, unquote_plus(value)))
    for value in sorted(secrets, key=len, reverse=True):
        result = result.replace(value, "[REDACTED]")
    return result


def fetch_dataset(settings: DataSettings, group_id: str | None = None, *, client: httpx.Client | None = None) -> dict:
    group = _required_string(group_id if group_id is not None else settings.group_id, "group_id")
    if not settings.api_url:
        raise DatasetError("DATASET_NOT_CONFIGURED", "DATASET_API_URL не задан в .env.")
    try:
        url = httpx.URL(settings.api_url).copy_merge_params({"group_id": group})
        if url.scheme not in ("http", "https") or not url.host or url.userinfo or url.fragment:
            raise ValueError()
    except (httpx.InvalidURL, ValueError):
        raise DatasetError("DATASET_INVALID_URL", "DATASET_API_URL содержит недопустимый адрес.") from None
    try:
        context = nullcontext(client) if client is not None else httpx.Client(timeout=60, follow_redirects=False)
        with context as http_client:
            with http_client.stream("GET", url) as response:
                if not 200 <= response.status_code < 300:
                    raise DatasetError("DATASET_HTTP_ERROR", f"API организаторов вернул HTTP {response.status_code}.")
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.max_dataset_bytes:
                        raise DatasetError("DATASET_TOO_LARGE", "Набор превышает MAX_DATASET_BYTES. Данные не обрезались и не импортированы.")
                    chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise DatasetError("DATASET_INVALID_JSON", "API вернул некорректный JSON.") from None
        try:
            parse_dataset(payload, expected_group_id=group)
        except DatasetError as exc:
            raise DatasetError(exc.code, _safe_error(str(exc), url)) from None
        return payload
    except httpx.TimeoutException:
        raise DatasetError("DATASET_TIMEOUT", "Таймаут API организаторов (60 секунд).") from None
    except httpx.RequestError:
        raise DatasetError("DATASET_CONNECTION_ERROR", "Не удалось подключиться к API организаторов.") from None


def load_dataset(conn: sqlite3.Connection, settings: DataSettings, group_id: str | None = None, *, client: httpx.Client | None = None) -> dict:
    group = group_id if group_id is not None else settings.group_id
    return import_dataset(conn, fetch_dataset(settings, group, client=client), expected_group_id=group)


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result.pop("raw_json")
    result["transcript"] = [dict(m) for m in conn.execute(
        "SELECT message_id,timestamp,speaker,text,offset_sec FROM messages WHERE conversation_id=? ORDER BY position",
        (conversation_id,),
    )]
    return result
