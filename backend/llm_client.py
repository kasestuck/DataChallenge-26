"""Audited model calls with bounded retries and exact known-token accounting."""

import hashlib
import random
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from backend.config import LLMOptions, MetaSettings, load_llm_options, load_settings
from backend.db import write_transaction
from backend.meta_client import ModelRequestError, request_completion


def _call_cost(usage: dict | None, options: LLMOptions) -> str | None:
    if options.price_input_per_1m is None or options.price_output_per_1m is None or not usage:
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if type(prompt) is not int or type(completion) is not int:
        return None
    return str((Decimal(prompt) * options.price_input_per_1m +
                Decimal(completion) * options.price_output_per_1m) / Decimal(1_000_000))


class LLMClient:
    def __init__(self, conn: sqlite3.Connection, settings: MetaSettings | None = None,
                 options: LLMOptions | None = None):
        self.conn = conn
        self.settings = settings or load_settings()
        self.options = options or load_llm_options()
        fingerprint = hashlib.sha256((self.settings.base_url.rstrip("/") + "\n" + self.settings.model).encode()).hexdigest()
        self._capability_key = "llm_json_mode_unsupported:" + fingerprint

    def _json_mode(self) -> bool:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key=?", (self._capability_key,)).fetchone()
        return row is None or row[0] != "true"

    def _disable_json_mode(self) -> None:
        with write_transaction(self.conn):
            self.conn.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self._capability_key, "true"),
            )

    def _record(self, *, evaluation_id, appeal_id, purpose, duration_ms, json_mode,
                completion: dict | None, error: ModelRequestError | None, local_attempt: int) -> str:
        call_id = str(uuid4())
        usage = (completion or {}).get("usage") or {}
        def token(name):
            value = usage.get(name)
            return value if type(value) is int and value >= 0 else None

        with write_transaction(self.conn):
            if evaluation_id is not None:
                attempt = self.conn.execute(
                    "SELECT COALESCE(MAX(attempt),0)+1 FROM llm_calls WHERE evaluation_id=?", (evaluation_id,)
                ).fetchone()[0]
            elif appeal_id is not None:
                attempt = self.conn.execute(
                    "SELECT COALESCE(MAX(attempt),0)+1 FROM llm_calls WHERE appeal_id=?", (appeal_id,)
                ).fetchone()[0]
            else:
                attempt = local_attempt
            self.conn.execute(
                """INSERT INTO llm_calls(call_id,evaluation_id,appeal_id,purpose,model,attempt,http_status,
                error_code,duration_ms,prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,
                price_output_per_1m,cost,created_at,raw_response,finish_reason,response_format_used)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (call_id, evaluation_id, appeal_id, purpose,
                 (completion or {}).get("model") or self.settings.model, attempt,
                 error.status_code if error else (completion or {}).get("http_status", 200),
                 error.code if error else None, duration_ms,
                 token("prompt_tokens"), token("completion_tokens"), token("total_tokens"),
                 str(self.options.price_input_per_1m) if self.options.price_input_per_1m is not None else None,
                 str(self.options.price_output_per_1m) if self.options.price_output_per_1m is not None else None,
                 _call_cost(usage, self.options), datetime.now(timezone.utc).isoformat(),
                 (completion or {}).get("content"), (completion or {}).get("finish_reason"), int(json_mode)),
            )
        return call_id

    def complete(self, system_prompt: str, user_prompt: str, *, evaluation_id: str | None = None,
                 appeal_id: str | None = None, purpose: str = "evaluation") -> dict:
        if purpose not in {"evaluation", "retry", "appeal"}:
            raise ValueError("Unsupported LLM purpose")
        if len(system_prompt) + len(user_prompt) > self.options.max_input_chars:
            raise ModelRequestError("MODEL_INPUT_TOO_LARGE", "Вход превышает LLM_MAX_INPUT_CHARS; данные не обрезались.")
        transport_attempt = 0
        physical_attempt = 0
        json_mode = self._json_mode()
        while True:
            physical_attempt += 1
            transport_attempt += 1
            completion, error = None, None
            started = time.monotonic()
            try:
                completion = request_completion(
                    system_prompt, user_prompt, settings=self.settings, max_tokens=self.options.max_tokens,
                    json_mode=json_mode, timeout_seconds=self.options.timeout_seconds,
                    use_system_trust=self.options.use_system_trust,
                )
            except ModelRequestError as exc:
                error = exc
                completion = exc.completion
            except Exception:
                error = ModelRequestError("MODEL_SDK_ERROR", "Неожиданная ошибка клиента модели; результат не получен.")
            elapsed = max(0, int((time.monotonic() - started) * 1000))
            call_id = self._record(
                evaluation_id=evaluation_id, appeal_id=appeal_id,
                purpose=purpose if physical_attempt == 1 else "retry",
                duration_ms=elapsed, json_mode=json_mode, completion=completion,
                error=error, local_attempt=physical_attempt,
            )
            if error is None:
                return {**completion, "call_id": call_id}
            if json_mode and error.response_format_unsupported:
                self._disable_json_mode()
                json_mode = False
                # The rejected capability probe does not consume a transient retry.
                transport_attempt -= 1
                continue
            limit = 4 if error.status_code == 429 else (
                3 if (error.status_code is not None and error.status_code >= 500) or error.code == "MODEL_TIMEOUT" else 1
            )
            if transport_attempt >= limit:
                raise error
            delay = max(float(2 ** (transport_attempt - 1)) + random.uniform(0, 1), error.retry_after or 0)
            # Short sleep chunks leave room for interruption without violating Retry-After.
            while delay > 0:
                chunk = min(delay, 30.0)
                time.sleep(chunk)
                delay -= chunk


def summarize_calls(conn: sqlite3.Connection, evaluation_id: str) -> dict:
    rows = conn.execute(
        "SELECT * FROM llm_calls WHERE evaluation_id=? ORDER BY attempt,created_at", (evaluation_id,)
    ).fetchall()
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    usage_complete = bool(rows) and all(all(row[key] is not None for key in keys) for row in rows)
    configured = bool(rows) and all(row["price_input_per_1m"] is not None and row["price_output_per_1m"] is not None for row in rows)
    cost_complete = configured and all(row["cost"] is not None for row in rows)
    costs = [Decimal(row["cost"]) for row in rows if row["cost"] is not None]
    return {
        "calls": len(rows),
        "successful_calls": sum(row["error_code"] is None for row in rows),
        "failed_calls": sum(row["error_code"] is not None for row in rows),
        **{key: sum(row[key] for row in rows if row[key] is not None)
           if any(row[key] is not None for row in rows) else None for key in keys},
        "usage_complete": usage_complete,
        "cost": str(sum(costs, Decimal(0))) if cost_complete else None,
        "known_cost": str(sum(costs, Decimal(0))) if costs else None,
        "cost_complete": cost_complete,
        "price_status": "configured" if configured else "price_not_set",
    }
