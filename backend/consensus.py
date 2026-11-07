"""Optional CLI consensus from three audited runs; REST evaluations stay individual."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backend.batch_reporting import run_batch_report, usage_from_calls
from backend.checklist import get_checklist
from backend.config import PROJECT_ROOT, read_environment
from backend.dataset import get_conversation
from backend.db import connect_db
from backend.evaluator import PROMPT_PATH, build_prompts, get_evaluation
from backend.jobs import get_job
from backend.scoring import calculate_scores
from backend.submission import (_error_record, _export_ok, _models_for_evaluation,
                                SubmissionError, validate_official_checklist, validate_submission)


def aggregate_three(evaluations, conversation, checklist, prompt_hash):
    """A criterion needs two matching valid votes. Never average scale values."""
    active = [c for c in checklist["criteria"] if c.get("active", True)]
    if len(evaluations) != 3:
        raise ValueError("Консенсус требует ровно три независимые попытки.")
    evaluation_ids = [e["evaluation_id"] for e in evaluations if e]
    if len(evaluation_ids) != len(set(evaluation_ids)):
        raise SubmissionError("CONSENSUS_DUPLICATE_RUN", "Одна и та же оценка не может считаться несколькими независимыми голосами.")
    usable = []
    for evaluation in evaluations:
        if not evaluation or evaluation.get("status") != "completed":
            continue
        snapshot = evaluation.get("conversation_snapshot", {})
        if (evaluation.get("conversation_id") != conversation["conversation_id"]
                or evaluation.get("checklist_version_id") != checklist["checklist_version_id"]
                or evaluation.get("prompt_hash") != prompt_hash
                or snapshot.get("transcript") != conversation["transcript"]):
            raise SubmissionError("CONSENSUS_INPUT_CHANGED", "Три прогона относятся к разным входным данным, версиям или промптам.")
        try:
            _export_ok(evaluation, conversation, checklist)
        except (SubmissionError, ValueError, KeyError, TypeError):
            continue
        usable.append(evaluation)
    selected, details, disputed = [], {}, []
    for definition in active:
        cid = definition["criterion_id"]
        candidates = [(evaluation, row) for evaluation in usable for row in evaluation.get("criteria", [])
                      if row.get("criterion_id") == cid and row.get("valid") is True]
        counts = Counter(row.get("answer") for _, row in candidates)
        winner = next((answer for answer, count in counts.items() if count >= 2), None)
        details[cid] = {"votes": dict(counts), "answer": winner, "source_evaluation_id": None}
        if winner is None:
            disputed.append(cid)
            continue
        evaluation, row = next((evaluation, row) for evaluation, row in candidates if row.get("answer") == winner)
        selected.append(copy.deepcopy(row))
        details[cid]["source_evaluation_id"] = evaluation["evaluation_id"]
    if disputed:
        return _error_record(conversation["conversation_id"],
            "Нет большинства двух из трёх валидных ответов по критериям: " + ", ".join(disputed) + "."), details
    if not usable:
        return _error_record(conversation["conversation_id"], "Не получены валидные ответы для консенсуса."), details
    scores = calculate_scores(checklist, {row["criterion_id"]: row["answer"] for row in selected})
    combined = copy.deepcopy(usable[0])
    combined.update(criteria=selected, total=scores["total"], final_total=scores["total"],
                    gross_violation=scores["gross_violation"])
    try:
        return _export_ok(combined, conversation, checklist), details
    except (SubmissionError, ValueError, KeyError, TypeError):
        return _error_record(conversation["conversation_id"], "Консенсус не прошёл проверку доказательств и пересчёта."), details


def _save_manifest(path, value):
    temporary = path.parent / (".consensus-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only our newly created temporary file.


def _apply_jobs(conn, manifest):
    """Recover completed rounds; never blindly re-send an uncertain paid request."""
    for item in manifest["jobs"]:
        if item.get("applied"):
            continue
        job = get_job(conn, item["job_id"])
        if job["status"] in {"pending", "running"}:
            raise SubmissionError("CONSENSUS_UNFINISHED_JOB", "Предыдущая задача ещё не завершена. Проверьте job_id из отчёта; новый платный прогон не запущен.")
        by_id = {entry["conversation_id"]: entry for entry in job["items"]}
        for cid in item["conversation_ids"]:
            entry = by_id.get(cid) or {}
            manifest["entries"][cid]["attempts"].append({
                "evaluation_id": entry.get("evaluation_id"), "job_id": item["job_id"],
                "error_code": entry.get("error_code"), "error_message": entry.get("error_message")})
        item["applied"] = True


def _cards(conn, attempts):
    cards = []
    for item in attempts:
        if not item.get("evaluation_id"):
            cards.append(None)
            continue
        card = get_evaluation(conn, item["evaluation_id"])
        # Human appeal decisions are not extra model votes, even on cache reuse.
        original = card.get("original") or {}
        for key in ("criteria", "total", "gross_violation", "reevaluation", "status"):
            if key in original:
                card[key] = copy.deepcopy(original[key])
        card["final_total"] = card["original_total"]
        cards.append(card)
    return cards


def _calls_for_jobs(conn, job_ids):
    if not job_ids:
        return []
    placeholders = ",".join("?" for _ in job_ids)
    return [dict(row) for row in conn.execute(
        "SELECT l.* FROM llm_calls l JOIN evaluations e ON e.evaluation_id=l.evaluation_id "
        f"WHERE e.job_id IN ({placeholders}) ORDER BY l.created_at,l.attempt", list(job_ids))]


def _model_settings_hash(env):
    return hashlib.sha256((str(env.get("META_MODEL")) + "\n" + str(env.get("META_BASE_URL"))).encode()).hexdigest()


def _input_prompt_hash(conversation, checklist, output_language):
    system, user = build_prompts(conversation, checklist, output_language)
    return hashlib.sha256((system + "\0" + user).encode()).hexdigest()


def _dialogue_average(usage, count):
    return {"dialogues": count,
            "total_tokens": str(Decimal(usage["total_tokens"]) / count) if count and usage["usage_complete"] else None,
            "cost": str(Decimal(usage["cost"]) / count) if count and usage["cost_complete"] else None}


async def run_consensus_report(database_path, request, *, team_name, progress=None):
    """Three bounded batch rounds with persisted provenance and successful-result reuse."""
    ids = sorted(request["conversation_ids"])
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError("Для консенсуса нужны уникальные conversation_ids.")
    started = time.monotonic()
    prompt_hash = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    env = read_environment()
    with closing(connect_db(database_path)) as conn:
        checklist = get_checklist(conn, request.get("checklist_version_id"))
        validate_official_checklist(checklist)
        conversations = {cid: get_conversation(conn, cid) for cid in ids}
    if any(conversation is None for conversation in conversations.values()):
        raise ValueError("Один из диалогов отсутствует в БД.")
    descriptor = {"database": str(Path(database_path).resolve()), "version_id": checklist["checklist_version_id"],
        "prompt_hash": prompt_hash, "output_language": request.get("output_language", "ru"), "runs": 3,
        "model_settings_hash": _model_settings_hash(env),
        "conversations": [[cid, conversations[cid]["revision"], conversations[cid]["content_hash"]] for cid in ids],
        "input_prompt_hashes": {cid: _input_prompt_hash(conversations[cid], checklist, request.get("output_language", "ru")) for cid in ids}}
    key = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
    folder = PROJECT_ROOT / "data" / "reports" / "consensus-cache"
    folder.mkdir(parents=True, exist_ok=True)
    path, lock = folder / (key + ".json"), folder / (key + ".lock")
    try:
        with lock.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError:
        raise SubmissionError("CONSENSUS_LOCKED", "Этот консенсус уже запущен или прерван. Проверьте процесс перед удалением lock; повторных вызовов нет.") from None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "descriptor": descriptor, "entries": {cid: {"attempts": []} for cid in ids}, "jobs": []}
        if manifest.get("descriptor") != descriptor:
            raise SubmissionError("CONSENSUS_CACHE_INVALID", "Кеш не соответствует зафиксированным входным данным.")
        with closing(connect_db(database_path)) as conn:
            _apply_jobs(conn, manifest)
            skipped = []
            for cid in ids:
                attempts = manifest["entries"][cid]["attempts"]
                if len(attempts) == 3:
                    result, _ = aggregate_three(_cards(conn, attempts), conversations[cid], checklist, prompt_hash)
                    if result["status"] == "ok":
                        skipped.append(cid)
                    else:
                        # Retry only unsuccessful consensus; earlier spend remains in jobs.
                        manifest["entries"][cid]["attempts"] = []
                elif len(attempts) > 3:
                    raise SubmissionError("CONSENSUS_CACHE_INVALID", "Кеш содержит больше трёх попыток одного цикла.")
        _save_manifest(path, manifest)
        new_job_ids = []
        for round_number in range(1, 4):
            pending = [cid for cid in ids if cid not in skipped and len(manifest["entries"][cid]["attempts"]) < round_number]
            if not pending:
                continue
            if hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() != prompt_hash:
                raise SubmissionError("CONSENSUS_INPUT_CHANGED", "Промпт изменился во время консенсуса; продолжение остановлено.")
            if _model_settings_hash(read_environment()) != descriptor["model_settings_hash"]:
                raise SubmissionError("CONSENSUS_INPUT_CHANGED", "Настройки модели изменились между прогонами; продолжение остановлено.")
            with closing(connect_db(database_path)) as conn:
                for cid in pending:
                    current = get_conversation(conn, cid)
                    if current["revision"] != conversations[cid]["revision"] or current["content_hash"] != conversations[cid]["content_hash"]:
                        raise SubmissionError("CONSENSUS_INPUT_CHANGED", "Транскрипт изменился во время консенсуса; продолжение остановлено.")
                    if _input_prompt_hash(current, checklist, descriptor["output_language"]) != descriptor["input_prompt_hashes"][cid]:
                        raise SubmissionError("CONSENSUS_INPUT_CHANGED", "Полный промпт изменился между прогонами; продолжение остановлено.")
            def round_progress(event):
                job_id = event["job_id"]
                if job_id not in new_job_ids:
                    new_job_ids.append(job_id)
                    manifest["jobs"].append({"job_id": job_id, "conversation_ids": pending, "round": round_number, "applied": False})
                    _save_manifest(path, manifest)
                if progress:
                    return progress({**event, "round": round_number, "runs": 3})
            await run_batch_report(database_path, {"conversation_ids": pending,
                "checklist_version_id": checklist["checklist_version_id"],
                "output_language": descriptor["output_language"], "force": True}, progress=round_progress)
            with closing(connect_db(database_path)) as conn:
                _apply_jobs(conn, manifest)
            _save_manifest(path, manifest)
        results, details, model_names, evaluations, failed_ids = [], {}, set(), [], set()
        with closing(connect_db(database_path)) as conn:
            for cid in ids:
                cards = _cards(conn, manifest["entries"][cid]["attempts"])
                result, votes = aggregate_three(cards, conversations[cid], checklist, prompt_hash)
                results.append(result)
                if result["status"] == "error" and not any(card and card.get("status") == "completed" for card in cards):
                    failed_ids.add(cid)
                details[cid] = {"evaluation_ids": [card["evaluation_id"] for card in cards if card],
                    "attempts": manifest["entries"][cid]["attempts"], "criteria": votes, "reused": cid in skipped}
                for card in cards:
                    if card:
                        evaluations.append(card)
                        if result["status"] == "error" or card.get("status") == "completed":
                            model_names.update(_models_for_evaluation(conn, card))
            new_calls = _calls_for_jobs(conn, new_job_ids)
            all_calls = _calls_for_jobs(conn, [job["job_id"] for job in manifest["jobs"]])
            validation_retries = sum(max(0, len(get_evaluation(conn, eid).get("validation_attempts", [])) - 1)
                                     for eid in {call["evaluation_id"] for call in new_calls})
        if not model_names:
            raise SubmissionError("MODEL_PROVENANCE_UNKNOWN", "Фактическое имя модели не установлено из журнала; файл сдачи не создан.")
        payload = {"schema_version": "1.0", "team_name": team_name,
            "model": {"mode": "external_api", "name": ", ".join(sorted(model_names))}, "results": results}
        metadata = {"expected_ids": ids, "transcript_lengths": {cid: len(c["transcript"]) for cid, c in conversations.items()}}
        validate_submission(payload, **metadata)
        counts = Counter(result["status"] for result in results)
        new_usage, all_usage = usage_from_calls(new_calls), usage_from_calls(all_calls)
        return {"status": "completed", "runs": 3, "total": len(ids), "done": len(ids), "skipped": len(skipped),
            "counts": {"completed": counts["ok"], "needs_review": counts["error"] - len(failed_ids), "failed": len(failed_ids)},
            "elapsed_seconds": round(time.monotonic() - started, 3), "usage": new_usage,
            "usage_all_cycles": all_usage, "averages_new": _dialogue_average(new_usage, len(ids) - len(skipped)),
            "averages_all_cycles": _dialogue_average(all_usage, len(ids)),
            "llm_duration_seconds": round(sum(c["duration_ms"] for c in new_calls) / 1000, 3),
            "llm_duration_all_cycles_seconds": round(sum(c["duration_ms"] for c in all_calls) / 1000, 3),
            "validation_retries": validation_retries, "job_ids": new_job_ids,
            "all_job_ids": [job["job_id"] for job in manifest["jobs"]], "prompt_hash": prompt_hash,
            "checklist_version_id": checklist["checklist_version_id"], "cache_path": str(path),
            "errors": [{"conversation_id": r["conversation_id"], "message": r["error"]} for r in results if r["status"] == "error"],
            "ready_conversation_ids": [r["conversation_id"] for r in results if r["status"] == "ok"],
            "consensus": details, "submission": payload, "submission_metadata": metadata,
            "evaluations": evaluations,
            "note": "Консенсус только CLI/файла сдачи; REST-карточки хранят отдельные исходные прогоны. Никакие исходные ответы не изменены."}
    finally:
        lock.unlink()  # Only the lock exclusively created by this invocation.
