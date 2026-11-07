"""SQLite schema and connection lifecycle. No model or network dependency."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from backend.config import load_data_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    dataset_group_id TEXT,
    channel TEXT NOT NULL CHECK(channel IN ('agent chat','voice call')),
    language TEXT NOT NULL CHECK(language IN ('RU','KZ','EN')),
    audio_url TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    message_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 1),
    timestamp TEXT NOT NULL,
    speaker TEXT NOT NULL CHECK(speaker IN ('client','operator','bot','system')),
    text TEXT NOT NULL,
    offset_sec REAL,
    PRIMARY KEY(conversation_id,message_id),
    UNIQUE(conversation_id,position)
);
CREATE TABLE IF NOT EXISTS checklist_versions (
    checklist_version_id TEXT PRIMARY KEY,
    checklist_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    weight_sum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(checklist_id,version)
);
CREATE TRIGGER IF NOT EXISTS checklist_versions_no_update
BEFORE UPDATE ON checklist_versions BEGIN
    SELECT RAISE(ABORT,'Checklist versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS checklist_versions_no_delete
BEFORE DELETE ON checklist_versions BEGIN
    SELECT RAISE(ABORT,'Checklist versions are immutable');
END;
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    checklist_version_id TEXT REFERENCES checklist_versions(checklist_version_id),
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','interrupted')),
    total INTEGER NOT NULL DEFAULT 0 CHECK(total >= 0),
    done INTEGER NOT NULL DEFAULT 0 CHECK(done >= 0 AND done <= total),
    errors_json TEXT NOT NULL DEFAULT '[]',
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    checklist_version_id TEXT NOT NULL REFERENCES checklist_versions(checklist_version_id),
    conversation_revision INTEGER NOT NULL,
    conversation_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','needs_review','failed')),
    output_language TEXT NOT NULL DEFAULT 'ru' CHECK(output_language IN ('ru','kk','en')),
    prompt_hash TEXT,
    original_result_json TEXT,
    final_result_json TEXT,
    original_total TEXT,
    final_total TEXT,
    gross_violation INTEGER CHECK(gross_violation IN (0,1)),
    error_code TEXT,
    error_message TEXT,
    job_id TEXT REFERENCES jobs(job_id),
    raw_responses_json TEXT NOT NULL DEFAULT '[]',
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    validation_attempts_json TEXT NOT NULL DEFAULT '[]',
    usage_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluations_conversation_version
ON evaluations(conversation_id,checklist_version_id,created_at);
CREATE TABLE IF NOT EXISTS evaluation_criteria (
    evaluation_id TEXT NOT NULL REFERENCES evaluations(evaluation_id),
    criterion_id TEXT NOT NULL,
    original_answer TEXT,
    final_answer TEXT,
    reasoning TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    checked_range_json TEXT,
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(evaluation_id,criterion_id)
);
CREATE TABLE IF NOT EXISTS appeals (
    appeal_id TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','reviewed')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(evaluation_id,criterion_id)
        REFERENCES evaluation_criteria(evaluation_id,criterion_id)
);
CREATE INDEX IF NOT EXISTS appeals_status ON appeals(status,created_at);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    appeal_id TEXT NOT NULL UNIQUE REFERENCES appeals(appeal_id),
    decision TEXT NOT NULL CHECK(decision IN ('confirm','change')),
    original_answer TEXT,
    final_answer TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    decided_by TEXT NOT NULL CHECK(decided_by IN ('reviewer','ai_assist')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id TEXT PRIMARY KEY,
    evaluation_id TEXT REFERENCES evaluations(evaluation_id),
    appeal_id TEXT REFERENCES appeals(appeal_id),
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    http_status INTEGER,
    error_code TEXT,
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
    prompt_tokens INTEGER CHECK(prompt_tokens >= 0),
    completion_tokens INTEGER CHECK(completion_tokens >= 0),
    total_tokens INTEGER CHECK(total_tokens >= 0),
    price_input_per_1m TEXT,
    price_output_per_1m TEXT,
    cost TEXT,
    raw_response TEXT,
    finish_reason TEXT,
    response_format_used INTEGER CHECK(response_format_used IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_calls_evaluation ON llm_calls(evaluation_id);
CREATE TABLE IF NOT EXISTS job_items (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    conversation_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','skipped','failed','interrupted')),
    evaluation_id TEXT REFERENCES evaluations(evaluation_id),
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY(job_id,conversation_id)
);
"""


def connect_db(path: str | Path | None = None) -> sqlite3.Connection:
    target = path if path is not None else load_data_settings().database_path
    if str(target) != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def write_transaction(conn: sqlite3.Connection):
    """Atomic write, including when a service is called inside a caller transaction."""
    if conn.in_transaction:
        name = f"dataset_{uuid4().hex}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        else:
            conn.execute(f"RELEASE {name}")
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def init_db(path: str | Path | None = None) -> None:
    # Schema is idempotent; subsequent app startups preserve existing data/versions.
    from backend.checklist import seed_checklist

    conn = connect_db(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        seed_checklist(conn)
    finally:
        conn.close()


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add audit columns to existing databases without replacing records."""
    additions = {
        "evaluations": {
            "prompt_hash": "TEXT",
            "raw_responses_json": "TEXT NOT NULL DEFAULT '[]'",
            "validation_errors_json": "TEXT NOT NULL DEFAULT '[]'",
            "validation_attempts_json": "TEXT NOT NULL DEFAULT '[]'",
            "usage_json": "TEXT",
        },
        "llm_calls": {
            "raw_response": "TEXT",
            "finish_reason": "TEXT",
            "response_format_used": "INTEGER CHECK(response_format_used IN (0,1))",
        },
        "appeals": {
            "disputed_result_json": "TEXT",
            "disputed_total": "TEXT",
        },
        "decisions": {
            "before_total": "TEXT",
            "after_total": "TEXT",
        },
    }
    with write_transaction(conn):
        for table, columns in additions.items():
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
