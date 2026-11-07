"""Small API adapters for uploads, filtered lists and uniform safe errors."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from backend.dataset import get_conversation, import_dataset
from backend.evaluator import get_evaluation


class APIError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def error_status(code: str, fallback: int = 422) -> int:
    if code in {"MODEL_RATE_LIMITED"}:
        return 429
    if code in {"MODEL_TIMEOUT", "DATASET_TIMEOUT"}:
        return 504
    if code.endswith("NOT_CONFIGURED") or code == "CONFIGURATION_ERROR":
        return 503
    if code.startswith("MODEL_"):
        return 413 if code == "MODEL_INPUT_TOO_LARGE" else 502
    if code.endswith("NOT_FOUND"):
        return 404
    if code in {"DATASET_TOO_LARGE", "UPLOAD_TOO_LARGE"}:
        return 413
    if code in {"DATASET_HTTP_ERROR", "DATASET_CONNECTION_ERROR", "DATASET_API_ERROR", "DATASET_INVALID_JSON"}:
        return 502
    return fallback


def parse_plain_transcript(text: str) -> list[dict]:
    messages = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        speaker, separator, content = line.partition(":")
        speaker = speaker.strip().lower()
        if not separator or speaker not in {"client", "operator", "bot", "system"} or not content.strip():
            raise APIError("INVALID_TRANSCRIPT_TEXT", f"Строка {index}: ожидается client/operator/bot/system: текст.")
        # Unknown time is explicit; never fabricate MM:SS from the line number.
        messages.append({"timestamp": "", "speaker": speaker, "text": content.lstrip()})
    if not messages:
        raise APIError("INVALID_TRANSCRIPT_TEXT", "Транскрипт не содержит реплик.")
    return messages


def upload_conversation(conn: sqlite3.Connection, record: dict) -> dict:
    record = dict(record)
    record["conversation_id"] = record.get("conversation_id") or f"upload-{uuid4()}"
    if isinstance(record.get("transcript"), str):
        record["transcript"] = parse_plain_transcript(record["transcript"])
    outcome = import_dataset(conn, {
        "isSuccess": True, "error": "", "data": {
            "dataset_group_id": "UPLOAD", "total_records": 1, "records": [record],
        },
    })
    return {"conversation_id": record["conversation_id"], "import_result": outcome,
            "conversation": conversation_detail(conn, record["conversation_id"])}


def _latest_id(conn: sqlite3.Connection, conversation_id: str) -> str | None:
    row = conn.execute(
        "SELECT evaluation_id FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row[0] if row else None


def conversation_detail(conn: sqlite3.Connection, conversation_id: str) -> dict:
    conversation = get_conversation(conn, conversation_id)
    if conversation is None:
        raise APIError("CONVERSATION_NOT_FOUND", "Диалог не найден.", 404)
    latest = _latest_id(conn, conversation_id)
    conversation["latest_result"] = get_evaluation(conn, latest) if latest else None
    if latest:
        conversation["evaluation_stale"] = (
            conversation["latest_result"]["conversation_snapshot"]["revision"] != conversation["revision"]
        )
    for message in conversation["transcript"]:
        message["audio_url"] = conversation["audio_url"]
    return conversation


def conversation_history(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    if not conn.execute("SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone():
        raise APIError("CONVERSATION_NOT_FOUND", "Диалог не найден.", 404)
    return [get_evaluation(conn, row[0]) for row in conn.execute(
        "SELECT evaluation_id FROM evaluations WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC",
        (conversation_id,),
    ).fetchall()]


def conversation_list(conn: sqlite3.Connection, *, language=None, channel=None, status=None, q=None) -> list[dict]:
    rows = conn.execute("""
        SELECT c.conversation_id,c.dataset_group_id,c.language,c.channel,c.revision,c.updated_at,
               e.evaluation_id,e.status,e.final_total AS total,e.gross_violation,e.conversation_revision,
               EXISTS(SELECT 1 FROM appeals a JOIN evaluations ae ON a.evaluation_id=ae.evaluation_id
                      WHERE ae.conversation_id=c.conversation_id) AS has_appeal,
               EXISTS(SELECT 1 FROM appeals a JOIN evaluations ae ON a.evaluation_id=ae.evaluation_id
                      WHERE ae.conversation_id=c.conversation_id AND a.status='open') AS has_open_appeal
        FROM conversations c
        LEFT JOIN evaluations e ON e.evaluation_id=(
            SELECT candidate.evaluation_id FROM evaluations candidate
            WHERE candidate.conversation_id=c.conversation_id
            ORDER BY candidate.created_at DESC,candidate.rowid DESC LIMIT 1)
        ORDER BY c.conversation_id
    """).fetchall()
    result = []
    needle = q.casefold() if q else None
    for row in rows:
        item = dict(row)
        item["status"] = item["status"] or "not_evaluated"
        if language and item["language"] != language or channel and item["channel"] != channel or status and item["status"] != status:
            continue
        if needle and needle not in item["conversation_id"].casefold():
            texts = conn.execute("SELECT text FROM messages WHERE conversation_id=?", (item["conversation_id"],))
            if not any(needle in message[0].casefold() for message in texts):
                continue
        item["gross_violation"] = bool(item["gross_violation"]) if item["gross_violation"] is not None else None
        item["has_appeal"] = bool(item["has_appeal"])
        item["has_open_appeal"] = bool(item["has_open_appeal"])
        item["evaluation_stale"] = item["conversation_revision"] is not None and item["conversation_revision"] != item["revision"]
        result.append(item)
    return result
