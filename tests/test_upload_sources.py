"""Source isolation uses synthetic records in temporary databases only."""

import asyncio
import copy
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import jobs, llm_client
from backend.dataset import import_dataset
from backend.db import connect_db
from backend.jobs import BatchManager, JobError
from backend.main import create_app


def record(cid, language="EN", channel="agent chat"):
    return {"conversation_id": cid, "language": language, "channel": channel,
            "audio_url": None, "transcript": [
                {"timestamp": "00:00", "speaker": "client", "text": "Synthetic request."},
                {"timestamp": "00:01", "speaker": "operator", "text": "Synthetic response."},
            ]}


def seed(path, group, *records):
    with closing(connect_db(path)) as conn:
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {
            "dataset_group_id": group, "total_records": len(records), "records": list(records)}})


@pytest.fixture
def api(tmp_path, monkeypatch):
    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("Source-isolation checks must not call a model")

    monkeypatch.setattr(llm_client, "request_completion", unexpected_model_call)
    path = tmp_path / "upload-sources.sqlite3"
    with TestClient(create_app(path)) as client:
        yield client, path


def snapshot(path, cid):
    with closing(connect_db(path)) as conn:
        conversation = dict(conn.execute("SELECT * FROM conversations WHERE conversation_id=?", (cid,)).fetchone())
        messages = [dict(row) for row in conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY position", (cid,))]
    return conversation, messages


@pytest.mark.parametrize("group", ["DEV", "CONTROL_TEST", None])
def test_upload_cannot_replace_or_relabel_another_source(api, group):
    client, path = api
    original = record("synthetic-source-id")
    seed(path, group or "UNKNOWN_SOURCE", original)
    if group is None:
        with closing(connect_db(path)) as conn:
            conn.execute("UPDATE conversations SET dataset_group_id=NULL")
            conn.commit()
    before = snapshot(path, original["conversation_id"])
    changed = copy.deepcopy(original)
    changed["transcript"][1]["text"] = "Different synthetic response."
    for payload in (original, changed):
        response = client.post("/api/conversations/upload", json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "UPLOAD_ID_CONFLICT"
        assert snapshot(path, original["conversation_id"]) == before


def test_upload_is_tagged_and_idempotently_updated_only_within_upload(api):
    client, path = api
    payload = record("synthetic-user-id")
    created = client.post("/api/conversations/upload", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["conversation"]["dataset_group_id"] == "UPLOAD"
    assert created.json()["import_result"]["inserted"] == 1
    identical = client.post("/api/conversations/upload", json=payload)
    assert identical.json()["import_result"]["unchanged"] == 1
    payload["transcript"][1]["text"] = "Updated synthetic response."
    updated = client.post("/api/conversations/upload", json=payload)
    assert updated.status_code == 201, updated.text
    assert updated.json()["import_result"]["updated"] == 1
    assert updated.json()["conversation"]["dataset_group_id"] == "UPLOAD"
    assert updated.json()["conversation"]["revision"] == 2
    assert updated.json()["conversation"]["transcript"][1]["text"] == payload["transcript"][1]["text"]
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1


def test_list_filters_source_and_exposes_it_in_list_detail_and_openapi(api):
    client, path = api
    seed(path, "DEV", record("synthetic-dev-en"), record("synthetic-dev-ru", "RU", "voice call"))
    uploaded = client.post("/api/conversations/upload", json=record("synthetic-upload", channel="whatsapp"))
    assert uploaded.status_code == 201
    all_rows = client.get("/api/conversations").json()
    assert len(all_rows) == 3
    assert {row["dataset_group_id"] for row in all_rows} == {"DEV", "UPLOAD"}
    dev = client.get("/api/conversations", params={"dataset_group_id": "DEV"}).json()
    assert {row["conversation_id"] for row in dev} == {"synthetic-dev-en", "synthetic-dev-ru"}
    filtered = client.get("/api/conversations", params={
        "dataset_group_id": "DEV", "language": "RU", "channel": "voice call", "q": "synthetic"}).json()
    assert [row["conversation_id"] for row in filtered] == ["synthetic-dev-ru"]
    upload = client.get("/api/conversations", params={"dataset_group_id": "UPLOAD"}).json()
    assert [row["conversation_id"] for row in upload] == ["synthetic-upload"]
    detail = client.get("/api/conversations/synthetic-dev-en").json()
    assert detail["dataset_group_id"] == "DEV"
    assert client.get("/api/conversations", params={"dataset_group_id": "UNKNOWN"}).json() == []
    schema = client.get("/openapi.json").json()
    for model in ("ConversationListItem", "ConversationResponse", "BatchRequest"):
        assert "dataset_group_id" in schema["components"]["schemas"][model]["properties"]
    params = schema["paths"]["/api/conversations"]["get"]["parameters"]
    assert any(item["name"] == "dataset_group_id" for item in params)


def wait_job(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}")
        assert result.status_code == 200
        job = result.json()
        if job["status"] in {"completed", "failed", "interrupted"}:
            return job
        time.sleep(.01)
    pytest.fail("Synthetic batch did not finish")


@pytest.mark.parametrize("selection,expected", [
    ({"all": True}, {"synthetic-dev", "synthetic-control"}),
    ({"all": True, "dataset_group_id": "DEV"}, {"synthetic-dev"}),
    ({"all": True, "dataset_group_id": "UPLOAD"}, {"synthetic-upload"}),
    ({"conversation_ids": ["synthetic-upload"]}, {"synthetic-upload"}),
    ({"all": True, "dataset_group_id": "EMPTY_GROUP"}, set()),
])
def test_batch_selection_requires_explicit_upload_choice(api, monkeypatch, selection, expected):
    client, path = api
    seed(path, "DEV", record("synthetic-dev"))
    seed(path, "CONTROL_TEST", record("synthetic-control"))
    assert client.post("/api/conversations/upload", json=record("synthetic-upload")).status_code == 201
    called = []

    def evaluate(conn, cid, **kwargs):
        called.append(cid)
        return {"status": "completed"}

    monkeypatch.setattr(jobs, "evaluate_conversation", evaluate)
    response = client.post("/api/evaluations/batch", json=selection)
    assert response.status_code == 202, response.text
    final = wait_job(client, response.json()["job_id"])
    assert final["status"] == "completed" and final["errors"] == []
    assert final["done"] == final["total"] == len(expected)
    assert set(called) == expected and len(called) == len(expected)
    assert {item["conversation_id"] for item in final["items"]} == expected


@pytest.mark.parametrize("group", ["", " ", 123])
def test_invalid_batch_source_is_rejected_before_a_job_is_created(api, group):
    client, path = api
    response = client.post("/api/evaluations/batch", json={"all": True, "dataset_group_id": group})
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

    async def check_manager():
        manager = BatchManager(path)
        with pytest.raises(JobError) as exc:
            await manager.submit({"all": True, "dataset_group_id": group})
        assert exc.value.code == "INVALID_BATCH_SELECTION"
        await manager.shutdown()

    asyncio.run(check_manager())


def test_batch_rejects_ambiguous_ids_and_source_filter(api):
    client, path = api
    response = client.post("/api/evaluations/batch", json={
        "conversation_ids": ["synthetic-upload"], "dataset_group_id": "DEV"})
    assert response.status_code == 422
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("group", ["", " "])
def test_empty_list_source_is_rejected(api, group):
    client, _ = api
    response = client.get("/api/conversations", params={"dataset_group_id": group})
    assert response.status_code == 422
