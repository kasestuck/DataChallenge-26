import asyncio
import json
import threading
import time
from contextlib import closing

import pytest

from backend import jobs
from backend.checklist import get_checklist
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.jobs import BatchManager, JobError, get_job, recover_interrupted


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    init_db(path)
    records = [{"conversation_id": f"c{i}", "channel": "agent chat", "language": "RU", "transcript": [
        {"timestamp": "00:00", "speaker": "operator", "text": "Здравствуйте"}]} for i in range(5)]
    with closing(connect_db(path)) as conn:
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": len(records), "records": records}})
    return path


def test_batch_isolates_errors_limits_parallelism_and_does_not_block_loop(db_path, monkeypatch):
    lock, state = threading.Lock(), {"active": 0, "peak": 0, "heartbeat": 0}
    def evaluate(conn, cid, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(.06)
        with lock:
            state["active"] -= 1
        if cid == "c2":
            raise RuntimeError("sensitive provider body")
        return {"status": "completed"}
    monkeypatch.setattr(jobs, "evaluate_conversation", evaluate)
    async def run():
        manager = BatchManager(db_path, 2)
        response = await manager.submit({"all": True})
        while manager.tasks:
            state["heartbeat"] += 1
            await asyncio.sleep(.005)
        with closing(connect_db(db_path)) as conn:
            final = get_job(conn, response["job_id"])
        assert final["status"] == "completed" and final["done"] == final["total"] == 5
        assert len(final["errors"]) == 1 and final["errors"][0]["conversation_id"] == "c2"
        assert "sensitive" not in json.dumps(final)
        assert state["peak"] == 2 and state["heartbeat"] > 4
        await manager.shutdown()
    asyncio.run(run())


def test_cross_job_idempotence_force_and_revision(db_path, monkeypatch):
    calls = []
    def evaluate(conn, cid, **kwargs):
        calls.append(cid)
        eid = f"eval-{len(calls)}"
        conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,conversation_snapshot_json,status,job_id,created_at,updated_at) "
                     "VALUES (?,?,?,(SELECT revision FROM conversations WHERE conversation_id=?),'{}','completed',?,'now','now')",
                     (eid, cid, kwargs["checklist_version_id"], cid, kwargs["job_id"]))
        conn.commit()
        return {"evaluation_id": eid, "status": "completed"}
    monkeypatch.setattr(jobs, "evaluate_conversation", evaluate)
    async def run():
        manager = BatchManager(db_path)
        req = {"conversation_ids": ["c1", "c1"]}
        first, second = await manager.submit(req), await manager.submit(req)
        await asyncio.gather(*list(manager.tasks))
        assert len(calls) == 1
        with closing(connect_db(db_path)) as conn:
            assert get_job(conn, second["job_id"])["skipped"] == 1
        await manager.submit({**req, "force": True})
        await asyncio.gather(*list(manager.tasks))
        assert len(calls) == 2
        with closing(connect_db(db_path)) as conn:
            conn.execute("UPDATE conversations SET revision=revision+1 WHERE conversation_id='c1'")
            conn.commit()
        await manager.submit(req)
        await manager.shutdown()
        assert len(calls) == 3
    asyncio.run(run())


def test_recovery_keeps_existing_progress_and_interrupts_unfinished(db_path):
    with closing(connect_db(db_path)) as conn:
        vid = get_checklist(conn)["checklist_version_id"]
        conn.execute("INSERT INTO jobs(job_id,checklist_version_id,status,total,done,request_json,created_at,updated_at) VALUES ('j',?,'running',2,1,'{}','a','a')", (vid,))
        conn.executemany("INSERT INTO job_items(job_id,conversation_id,position,status) VALUES ('j',?,?,?)", [("c1", 0, "completed"), ("c2", 1, "running")])
        conn.commit()
        assert recover_interrupted(conn) == 1
        job = get_job(conn, "j")
        assert job["status"] == "interrupted" and job["done"] == 1
        assert job["items"][1]["status"] == "interrupted"
        assert recover_interrupted(conn) == 0


def test_unexpected_worker_failure_waits_for_siblings_and_finalizes_items(db_path, monkeypatch):
    completed = []
    original = BatchManager._evaluate
    def worker(self, job_id, cid, payload):
        if cid == "c1":
            raise RuntimeError("private storage details")
        time.sleep(.03)
        original(self, job_id, cid, payload)
        completed.append(cid)
    monkeypatch.setattr(BatchManager, "_evaluate", worker)
    monkeypatch.setattr(jobs, "evaluate_conversation", lambda *a, **kw: {"status": "completed"})
    async def run():
        manager = BatchManager(db_path)
        result = await manager.submit({"conversation_ids": ["c1", "c2", "c3"]})
        await manager.shutdown()
        assert set(completed) == {"c2", "c3"}
        with closing(connect_db(db_path)) as conn:
            job = get_job(conn, result["job_id"])
        assert job["status"] == "failed" and job["done"] == 3
        assert job["error_count"] == 1
        assert all(item["status"] not in {"pending", "running"} for item in job["items"])
        assert "private" not in json.dumps(job)
    asyncio.run(run())


@pytest.mark.parametrize("payload", [{}, {"all": True, "conversation_ids": ["c1"]}, {"conversation_ids": []}, {"all": True, "criterion_ids": ["missing"]}])
def test_invalid_batch(db_path, payload):
    async def run():
        with pytest.raises(JobError):
            await BatchManager(db_path).submit(payload)
    asyncio.run(run())
