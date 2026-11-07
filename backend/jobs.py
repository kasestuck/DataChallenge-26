"""Persistent batch progress; network work runs in threads behind a shared semaphore."""
from __future__ import annotations

import asyncio
import json
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from backend.checklist import get_checklist
from backend.config import ConfigurationError
from backend.db import connect_db, write_transaction
from backend.evaluator import EvaluationInputError, evaluate_conversation
from backend.meta_client import ModelRequestError


class JobError(ValueError):
    def __init__(self, code, message, status_code=422):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_job(conn, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise JobError("JOB_NOT_FOUND", "Задача не найдена.", 404)
    result = dict(row)
    result["errors"] = json.loads(result.pop("errors_json"))
    result["request"] = json.loads(result.pop("request_json"))
    result["error_count"] = len(result["errors"])
    result["items"] = [dict(r) for r in conn.execute(
        "SELECT conversation_id,status,evaluation_id,error_code,error_message FROM job_items WHERE job_id=? ORDER BY position",
        (job_id,),
    )]
    result["skipped"] = sum(item["status"] == "skipped" for item in result["items"])
    return result


def recover_interrupted(conn):
    """Do not resume unknown in-flight requests or silently charge for them twice."""
    with write_transaction(conn):
        ids = [r[0] for r in conn.execute("SELECT job_id FROM jobs WHERE status IN ('pending','running')")]
        for job_id in ids:
            conn.execute("UPDATE job_items SET status='interrupted',error_code='JOB_INTERRUPTED',error_message=? "
                         "WHERE job_id=? AND status IN ('pending','running')",
                         ("Сервер перезапущен до завершения задачи.", job_id))
            conn.execute("UPDATE evaluations SET status='failed',error_code='JOB_INTERRUPTED',error_message=?,updated_at=? "
                         "WHERE job_id=? AND status IN ('pending','running')",
                         ("Оценка прервана перезапуском сервера.", _now(), job_id))
            errors = [dict(r) for r in conn.execute(
                "SELECT conversation_id,error_code AS code,error_message AS message FROM job_items "
                "WHERE job_id=? AND error_code IS NOT NULL ORDER BY position", (job_id,))]
            conn.execute("UPDATE jobs SET status='interrupted',errors_json=?,updated_at=? WHERE job_id=?",
                         (json.dumps(errors, ensure_ascii=False), _now(), job_id))
    return len(ids)


class BatchManager:
    """One API process, many concurrent jobs; locks are shared across these jobs."""
    def __init__(self, database_path, max_concurrency=4):
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 64:
            raise ConfigurationError("MAX_CONCURRENCY должен быть целым числом от 1 до 64.")
        self.database_path = database_path
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.tasks = set()
        self.locks = {}
        self.stopping = False

    async def submit(self, request):
        if self.stopping:
            raise JobError("SERVER_STOPPING", "Сервер завершает работу.", 503)
        request = dict(request)
        supplied = request.get("conversation_ids")
        if bool(supplied) == bool(request.get("all", False)):
            raise JobError("INVALID_BATCH_SELECTION", "Укажите непустой conversation_ids либо all=true.")
        if supplied is not None and (not isinstance(supplied, list) or any(not isinstance(x, str) or not x.strip() for x in supplied)):
            raise JobError("INVALID_BATCH_SELECTION", "conversation_ids должен содержать строки с ID.")
        if request.get("output_language", "ru") not in {"ru", "kk", "en"}:
            raise JobError("INVALID_OUTPUT_LANGUAGE", "Язык объяснений должен быть ru, kk или en.")
        with closing(connect_db(self.database_path)) as conn, write_transaction(conn):
            checklist = get_checklist(conn, request.get("checklist_version_id"))
            ids = list(dict.fromkeys(supplied)) if supplied else [r[0] for r in conn.execute(
                "SELECT conversation_id FROM conversations ORDER BY conversation_id")]
            selected = request.get("criterion_ids")
            if selected is not None:
                active_ids = {c["criterion_id"] for c in checklist["criteria"] if c.get("active", True)}
                if not isinstance(selected, list) or any(not isinstance(cid, str) or cid not in active_ids for cid in selected):
                    raise JobError("INVALID_CRITERION_IDS", "criterion_ids содержит неизвестный или неактивный критерий.")
                request["criterion_ids"] = list(dict.fromkeys(selected))
            request["checklist_version_id"] = checklist["checklist_version_id"]
            job_id, now = str(uuid4()), _now()
            conn.execute("INSERT INTO jobs(job_id,checklist_version_id,status,total,request_json,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?)", (job_id, checklist["checklist_version_id"], "pending", len(ids),
                                                   json.dumps(request, ensure_ascii=False), now, now))
            conn.executemany("INSERT INTO job_items(job_id,conversation_id,position,status) VALUES (?,?,?,'pending')",
                             [(job_id, cid, pos) for pos, cid in enumerate(ids)])
            result = get_job(conn, job_id)
        task = asyncio.create_task(self._run(job_id, ids, request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return result

    def _status(self, job_id, status):
        with closing(connect_db(self.database_path)) as conn, write_transaction(conn):
            conn.execute("UPDATE jobs SET status=?,updated_at=? WHERE job_id=?", (status, _now(), job_id))

    async def _run(self, job_id, ids, request):
        try:
            self._status(job_id, "running")
            outcomes = await asyncio.gather(*(self._item(job_id, cid, request) for cid in ids), return_exceptions=True)
            if any(isinstance(outcome, BaseException) for outcome in outcomes):
                self._fail_unfinished(job_id)
            else:
                self._status(job_id, "completed")
        except asyncio.CancelledError:
            self._status(job_id, "interrupted")
            raise
        except Exception:
            # A storage failure may prevent recording until the next startup;
            # recovery will mark the persisted running job interrupted then.
            try:
                self._fail_unfinished(job_id)
            except Exception:
                pass

    def _fail_unfinished(self, job_id):
        with closing(connect_db(self.database_path)) as conn, write_transaction(conn):
            conn.execute("UPDATE job_items SET status='failed',error_code='INTERNAL_ERROR',error_message=? "
                         "WHERE job_id=? AND status IN ('pending','running')",
                         ("Работник задачи завершился с ошибкой; оценка не подменялась.", job_id))
            errors = [dict(r) for r in conn.execute(
                "SELECT conversation_id,error_code AS code,error_message AS message FROM job_items "
                "WHERE job_id=? AND error_code IS NOT NULL ORDER BY position", (job_id,))]
            conn.execute("UPDATE jobs SET status='failed',done=total,errors_json=?,updated_at=? WHERE job_id=?",
                         (json.dumps(errors, ensure_ascii=False), _now(), job_id))

    async def _item(self, job_id, cid, request):
        key = (cid, request["checklist_version_id"])
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock, self.semaphore:
            await asyncio.to_thread(self._evaluate, job_id, cid, request)

    def _evaluate(self, job_id, cid, request):
        with closing(connect_db(self.database_path)) as conn:
            with write_transaction(conn):
                conn.execute("UPDATE job_items SET status='running' WHERE job_id=? AND conversation_id=?", (job_id, cid))
            result, error, state = None, None, "completed"
            try:
                previous = None
                if not request.get("force", False):
                    previous = conn.execute("SELECT e.evaluation_id FROM evaluations e JOIN conversations c "
                                            "ON c.conversation_id=e.conversation_id WHERE e.conversation_id=? "
                                            "AND e.checklist_version_id=? AND e.conversation_revision=c.revision "
                                            "AND e.status='completed' ORDER BY e.created_at DESC LIMIT 1",
                                            (cid, request["checklist_version_id"])).fetchone()
                if previous:
                    result, state = {"evaluation_id": previous[0]}, "skipped"
                else:
                    result = evaluate_conversation(conn, cid, checklist_version_id=request["checklist_version_id"],
                                                   output_language=request.get("output_language", "ru"),
                                                   criterion_ids=request.get("criterion_ids"), job_id=job_id)
                    if result["status"] != "completed":
                        error = result.get("error") or {"code": "EVALUATION_NEEDS_REVIEW", "message": "Ответ модели требует проверки."}
                        state = "failed"
            except (EvaluationInputError, ModelRequestError, ConfigurationError, LookupError) as exc:
                error = {"code": getattr(exc, "code", "CONFIGURATION_ERROR" if isinstance(exc, ConfigurationError) else "NOT_FOUND"),
                         "message": str(exc)}
                state = "failed"
            except Exception:
                error = {"code": "INTERNAL_ERROR", "message": "Не удалось завершить оценку; фиктивный результат не создан."}
                state = "failed"
            with write_transaction(conn):
                conn.execute("UPDATE job_items SET status=?,evaluation_id=?,error_code=?,error_message=? "
                             "WHERE job_id=? AND conversation_id=?",
                             (state, (result or {}).get("evaluation_id"), (error or {}).get("code"),
                              (error or {}).get("message"), job_id, cid))
                errors = [dict(r) for r in conn.execute(
                    "SELECT conversation_id,error_code AS code,error_message AS message FROM job_items "
                    "WHERE job_id=? AND error_code IS NOT NULL ORDER BY position", (job_id,))]
                done = conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id=? AND status IN ('completed','skipped','failed')",
                                    (job_id,)).fetchone()[0]
                conn.execute("UPDATE jobs SET done=?,errors_json=?,updated_at=? WHERE job_id=?",
                             (done, json.dumps(errors, ensure_ascii=False), _now(), job_id))

    async def shutdown(self):
        self.stopping = True
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
