"""Run persisted batches and report new spend separately from reused evaluations."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backend.config import ConfigurationError, PROJECT_ROOT, read_environment
from backend.db import connect_db, init_db
from backend.evaluator import get_evaluation
from backend.jobs import BatchManager, get_job


def usage_from_calls(rows):
    rows = list(rows)
    token_keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    configured = bool(rows) and all(r["price_input_per_1m"] is not None and r["price_output_per_1m"] is not None for r in rows)
    costs = [Decimal(r["cost"]) for r in rows if r["cost"] is not None]
    complete = not rows or configured and len(costs) == len(rows)
    return {
        "calls": len(rows), "successful_calls": sum(r["error_code"] is None for r in rows),
        "failed_calls": sum(r["error_code"] is not None for r in rows),
        "retry_calls": sum(r["purpose"] == "retry" for r in rows),
        **{key: sum(r[key] for r in rows if r[key] is not None) if any(r[key] is not None for r in rows) else (0 if not rows else None) for key in token_keys},
        "usage_complete": all(all(r[key] is not None for key in token_keys) for r in rows),
        "cost": str(sum(costs, Decimal(0))) if complete else None,
        "known_cost": str(sum(costs, Decimal(0))) if costs or not rows else None,
        "cost_complete": complete,
        "price_status": "configured" if configured else "price_not_set",
        "price_message": "цена задана" if configured else "цена не задана",
    }


def _average(evaluations):
    count = len(evaluations)
    result = {"evaluation_count": count, "basis": "Все выбранные оценки, включая failed/needs_review; расходы соответствуют указанным evaluation_id."}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
        values = [r["usage"].get(key) for r in evaluations]
        result[key] = format(sum((Decimal(str(v)) for v in values), Decimal(0)) / count, "f") if count and all(v is not None for v in values) else None
    result["usage_complete"] = bool(count) and all(r["usage"].get("usage_complete", False) for r in evaluations)
    result["cost_complete"] = bool(count) and all(r["usage"].get("cost_complete", False) for r in evaluations)
    return result


def collect_batch_report(conn, job_id, elapsed_seconds):
    job = get_job(conn, job_id)
    evaluations = []
    for item in job["items"]:
        if item["evaluation_id"]:
            evaluations.append(get_evaluation(conn, item["evaluation_id"]))
    by_id = {r["evaluation_id"]: r for r in evaluations}
    counts = {"completed": 0, "needs_review": 0, "failed": 0}
    for item in job["items"]:
        result = by_id.get(item["evaluation_id"])
        state = result["status"] if result else "failed"
        counts[state if state in counts else "failed"] += 1
    rows = conn.execute("SELECT l.* FROM llm_calls l JOIN evaluations e ON l.evaluation_id=e.evaluation_id WHERE e.job_id=? ORDER BY l.created_at,l.attempt", (job_id,)).fetchall()
    new_ids = {item["evaluation_id"] for item in job["items"] if item["status"] != "skipped" and item["evaluation_id"]}
    new_evaluations = [r for r in evaluations if r["evaluation_id"] in new_ids]
    review = [{"conversation_id": r["conversation_id"], "evaluation_id": r["evaluation_id"], "validation_errors": r.get("validation_errors", [])} for r in evaluations if r["status"] == "needs_review"]
    prompt_hashes = sorted({r.get("prompt_hash") or "legacy_unknown" for r in evaluations})
    return {
        "job_id": job_id, "status": job["status"], "total": job["total"], "done": job["done"],
        "skipped": job["skipped"], "counts": counts, "elapsed_seconds": round(elapsed_seconds, 3),
        "usage": usage_from_calls(rows), "usage_basis": "Только физические вызовы, принадлежащие этой задаче; пропущенные оценки не оплачиваются повторно.",
        "validation_retries": sum(max(0, len(r.get("validation_attempts", [])) - 1) for r in new_evaluations),
        "errors": job["errors"], "needs_review": review,
        "ready_conversation_ids": [r["conversation_id"] for r in evaluations if r["status"] == "completed"],
        "prompt_hashes": prompt_hashes,
        "per_evaluation": [{"conversation_id": r["conversation_id"], "evaluation_id": r["evaluation_id"], "status": r["status"], "total": r.get("total"), "usage": r["usage"], "new_in_job": r["evaluation_id"] in new_ids} for r in evaluations],
        "averages_new": _average(new_evaluations), "averages_selected": _average(evaluations),
        "items": job["items"], "evaluations": evaluations,
    }


async def run_batch_report(database_path, request, progress=None):
    """Await every worker, persist progress, and keep model failures explicit."""
    init_db(database_path)
    env = read_environment()
    try:
        concurrency = int(env.get("MAX_CONCURRENCY") or 4)
    except (TypeError, ValueError):
        raise ConfigurationError("MAX_CONCURRENCY должен быть целым числом от 1 до 64.") from None
    manager = BatchManager(database_path, concurrency)
    started = time.monotonic()
    submitted = await manager.submit(request)
    last = None
    try:
        while True:
            with closing(connect_db(database_path)) as conn:
                job = get_job(conn, submitted["job_id"])
            signature = (job["status"], job["done"], job["error_count"])
            if signature != last and progress:
                event = {"job_id": job["job_id"], "status": job["status"], "done": job["done"], "total": job["total"], "error_count": job["error_count"], "elapsed_seconds": round(time.monotonic() - started, 3)}
                response = progress(event)
                if inspect.isawaitable(response):
                    await response
            last = signature
            if job["status"] in {"completed", "failed", "interrupted"}:
                break
            if not manager.tasks:
                # A storage failure can leave a running row; do not wait forever.
                raise RuntimeError("Batch worker stopped without persisting a terminal state.")
            await asyncio.sleep(.2)
    finally:
        await manager.shutdown()
    with closing(connect_db(database_path)) as conn:
        return collect_batch_report(conn, submitted["job_id"], time.monotonic() - started)


def make_report_directory(prefix, requested=None):
    """Keep transcripts and model evidence under the ignored reports directory."""
    root = (PROJECT_ROOT / "data" / "reports").resolve()
    target = Path(requested).resolve() if requested else root / (prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
    if not target.is_relative_to(root):
        raise ValueError("Отчёты с данными диалогов должны находиться внутри data/reports/.")
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_json_report(path, value):
    # A provider should never echo credentials, but do not export one even if it does.
    secret = read_environment().get("MODEL_API_KEY") or ""
    content = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if secret:
        content = content.replace(secret, "[REDACTED]")
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(content)


def compact_report(report):
    return {k: v for k, v in report.items() if k not in {"evaluations", "items"}}
