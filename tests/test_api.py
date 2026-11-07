"""REST integration tests. The only substitute for model transport is inside tests."""

import copy
import json
import time
from contextlib import closing
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend import llm_client
from backend.db import connect_db
from backend.main import create_app
from backend.meta_client import ModelRequestError


@pytest.fixture
def api(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("META_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("META_MODEL", "test-model")
    monkeypatch.setenv("DATASET_API_URL", "")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")

    def request(system, user, **kwargs):
        calls.append(user)
        lines = user.splitlines()
        metadata = json.loads(lines[1])
        config = json.loads(lines[3])
        answers = []
        for criterion in config["criteria"]:
            answer = "no" if criterion["type"] == "flag" else (
                max(criterion["scale"], key=Decimal) if criterion["type"] == "scale" else "yes"
            )
            answers.append({"criterion_id": criterion["criterion_id"], "answer": answer,
                            "reasoning": "Действие найдено в реплике оператора.",
                            "evidence": [] if answer == "no" else [{"message_id": "m2", "quote": "Здравствуйте, меня зовут Анна."}],
                            "checked_range": {"from": "m1", "to": "m2"} if answer == "no" else None})
        return {"content": json.dumps({"conversation_id": metadata["conversation_id"], "criteria": answers}, ensure_ascii=False),
                "model": "test-model", "finish_reason": "stop", "http_status": 200,
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}

    monkeypatch.setattr(llm_client, "request_completion", request)
    path = tmp_path / "api.sqlite3"
    with TestClient(create_app(path)) as client:
        yield client, path, calls


def upload(client, cid="test-1", language="RU", channel="agent chat"):
    result = client.post("/api/conversations/upload", json={
        "conversation_id": cid, "language": language, "channel": channel,
        "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Тарифы брокера?"},
                       {"timestamp": "00:02", "speaker": "operator", "text": "Здравствуйте, меня зовут Анна."}],
    })
    assert result.status_code == 201, result.text
    return result.json()


def wait_job(client, job_id):
    statuses = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}")
        assert result.status_code == 200, result.text
        job = result.json()
        statuses.append(job["status"])
        if job["status"] in {"completed", "failed", "interrupted"}:
            return job, statuses
        time.sleep(.01)
    pytest.fail("Batch did not finish in ten seconds")


def test_health_openapi_cors_and_error_envelope(api):
    client, _, _ = api
    assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}
    schema = client.get("/openapi.json").json()
    assert "/api/evaluations/batch" in schema["paths"]
    assert "BatchRequest" in schema["components"]["schemas"]
    assert client.get("/docs").status_code == 200
    cors = client.options("/api/evaluations", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert cors.status_code == 200
    assert cors.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    for path in ("/api/conversations/missing", "/api/evaluations/missing", "/api/jobs/missing", "/no-such-route"):
        response = client.get(path)
        assert response.status_code == 404
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message"}
    invalid = client.post("/api/evaluations", json={"api_key": "secret-for-test"})
    assert invalid.status_code == 422
    assert "secret-for-test" not in invalid.text


def test_json_text_upload_normalization_and_idempotence(api):
    client, _, _ = api
    first = upload(client)
    second = upload(client)
    assert first["import_result"]["inserted"] == 1
    assert second["import_result"]["unchanged"] == 1
    detail = client.get("/api/conversations/test-1").json()
    assert detail["transcript"][1]["message_id"] == "m2"
    assert detail["transcript"][1]["timestamp"] == "00:02"
    assert detail["transcript"][1]["offset_sec"] is None
    assert detail["latest_result"] is None
    raw = client.post("/api/conversations/upload?conversation_id=plain&language=EN", content="client: Hi\noperator: Hello",
                      headers={"Content-Type": "text/plain; charset=utf-8"})
    assert raw.status_code == 201, raw.text
    assert raw.json()["conversation"]["transcript"][0]["timestamp"] == ""
    malformed = client.post("/api/conversations/upload", json={"transcript": "unknown: text"})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_TRANSCRIPT_TEXT"


@pytest.mark.parametrize("channel", ["email", "whatsapp", "custom support inbox"])
def test_arbitrary_channel_upload_evaluation_and_timestamp_preservation(api, channel):
    client, _, calls = api
    response = client.post("/api/conversations/upload", json={
        "conversation_id": "synthetic-omnichannel", "language": "RU", "channel": channel,
        "transcript": [{"speaker": "client", "text": "Где найти справку приложения?"},
                       {"timestamp": "Sat, 07 Nov 2026 14:00:00 +0500", "speaker": "operator", "text": "Здравствуйте, меня зовут Анна."},
                       {"timestamp": None, "speaker": "system", "text": "Тестовый диалог завершён."}],
    })
    assert response.status_code == 201, response.text
    messages = response.json()["conversation"]["transcript"]
    assert [m["timestamp"] for m in messages] == ["", "Sat, 07 Nov 2026 14:00:00 +0500", ""]
    assert [m["message_id"] for m in messages] == ["m1", "m2", "m3"]
    evaluated = client.post("/api/evaluations", json={"conversation_id": "synthetic-omnichannel"})
    assert evaluated.status_code == 201, evaluated.text
    assert evaluated.json()["status"] == "completed"
    metadata = json.loads(calls[0].splitlines()[1])
    assert metadata["channel"] == channel
    assert metadata["channel_context"]["evaluation_source"] == "transcript_only"
    listed = client.get("/api/conversations", params={"channel": channel}).json()
    assert any(c["conversation_id"] == "synthetic-omnichannel" for c in listed)


def test_no_operator_returns_explicit_error_without_llm_or_zero_score(api):
    client, path, calls = api
    uploaded = client.post("/api/conversations/upload", json={
        "conversation_id": "synthetic-no-operator", "channel": "email",
        "transcript": [{"speaker": "client", "text": "Здравствуйте."}],
    })
    assert uploaded.status_code == 201
    response = client.post("/api/evaluations", json={"conversation_id": "synthetic-no-operator"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_OPERATOR_MESSAGES"
    assert "total" not in response.json() and not calls
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0


def test_dataset_not_configured_and_model_error_is_not_success(api, monkeypatch):
    client, path, _ = api
    response = client.post("/api/dataset/load", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATASET_NOT_CONFIGURED"
    upload(client)

    def denied(*args, **kwargs):
        raise ModelRequestError("MODEL_ACCESS_DENIED", "HTTP 401: проверьте ключ и доступ к API/модели.", 401)

    monkeypatch.setattr(llm_client, "request_completion", denied)
    response = client.post("/api/evaluations", json={"conversation_id": "test-1"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_ACCESS_DENIED"
    saved = client.get("/api/evaluations/" + response.headers["X-Evaluation-ID"]).json()
    assert saved["status"] == "failed" and saved["total"] is None
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1


@pytest.mark.parametrize("code,status", [("MODEL_RATE_LIMITED", 429), ("MODEL_TIMEOUT", 504),
                                        ("MODEL_NOT_FOUND", 502), ("MODEL_UNAVAILABLE", 502)])
def test_model_errors_map_http(api, monkeypatch, code, status):
    client, _, _ = api
    upload(client)
    # Patch the evaluator boundary to avoid transport retry delays in a mapping test.
    monkeypatch.setattr("backend.main.evaluator.evaluate_conversation", lambda *args, **kwargs: {
        "evaluation_id": "persisted-id", "status": "failed", "error": {"code": code, "message": "Safe diagnostic"}})
    result = client.post("/api/evaluations", json={"conversation_id": "test-1"})
    assert result.status_code == status
    assert result.json()["error"]["code"] == code


def test_evaluation_history_filters_cost_and_alias(api):
    client, _, calls = api
    upload(client)
    upload(client, "en-1", "EN")
    upload(client, "voice-1", channel="voice call")
    result = client.post("/api/evaluate", json={"conversation_id": "test-1"})
    assert result.status_code == 201, result.text
    evaluated = result.json()
    assert evaluated["status"] == "completed" and evaluated["total"] == "100.00"
    assert evaluated["original"] == evaluated["final"]
    assert evaluated["criteria"][0]["evidence"][0]["speaker"] == "operator"
    assert len(calls) == 1
    assert client.get("/api/conversations/test-1/evaluations").json()[0]["evaluation_id"] == evaluated["evaluation_id"]
    assert len(client.get("/api/conversations?language=EN").json()) == 1
    assert len(client.get("/api/conversations", params={"channel": "voice call"}).json()) == 1
    assert len(client.get("/api/conversations?status=completed").json()) == 1
    assert len(client.get("/api/conversations?status=not_evaluated").json()) == 2
    assert len(client.get("/api/conversations", params={"q": "ТАРИФЫ"}).json()) == 3
    assert client.get("/api/conversations", params={"q": "test-1"}).json()[0]["total"] == "100.00"
    assert client.get("/api/stats/cost").status_code == 200


def test_checklist_version_validation_and_activation(api):
    client, _, _ = api
    original = client.get("/api/checklists/active").json()
    changed = copy.deepcopy(original)
    changed["criteria"][0]["weight"] = 12
    saved = client.post("/api/checklists", json=changed)
    assert saved.status_code == 201, saved.text
    saved = saved.json()
    assert saved["version"] == original["version"] + 1
    assert saved["weight_sum"] == "102"
    assert saved["change_summary"]["evaluation_required"] == []
    assert client.get("/api/checklists/active").json()["checklist_version_id"] == saved["checklist_version_id"]
    assert client.post("/api/checklists/" + original["checklist_version_id"] + "/activate").status_code == 200
    changed["criteria"][0]["weight"] = -1
    rejected = client.post("/api/checklists", json=changed)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "CHECKLIST_INVALID"
    assert len(client.get("/api/checklists").json()) == 2


def test_batch_api_progress_idempotence_force_and_failure_isolation(api, monkeypatch):
    client, _, calls = api
    upload(client, "a")
    upload(client, "b", "KZ")
    response = client.post("/api/evaluations/batch", json={"conversation_ids": ["a", "b", "missing"]})
    assert response.status_code == 202, response.text
    final, statuses = wait_job(client, response.json()["job_id"])
    assert final["done"] == final["total"] == 3
    assert final["status"] == "completed"
    assert len(final["errors"]) == 1 and final["errors"][0]["conversation_id"] == "missing"
    assert len(calls) == 2
    second = client.post("/api/evaluations/batch", json={"all": True})
    job, _ = wait_job(client, second.json()["job_id"])
    assert job["skipped"] == 2 and len(calls) == 2
    forced = client.post("/api/evaluations/batch", json={"all": True, "force": True})
    wait_job(client, forced.json()["job_id"])
    assert len(calls) == 4
    invalid = client.post("/api/evaluations/batch", json={"all": True, "conversation_ids": ["a"]})
    assert invalid.status_code == 422


def test_appeal_api_decision_preserves_original_and_marks_list(api):
    client, _, _ = api
    upload(client)
    evaluation = client.post("/api/evaluations", json={"conversation_id": "test-1"}).json()
    eid = evaluation["evaluation_id"]
    created = client.post("/api/appeals", json={"evaluation_id": eid, "criterion_id": "01", "comment": "Проверить первое приветствие."})
    assert created.status_code == 201, created.text
    aid = created.json()["appeal_id"]
    assert client.get("/api/conversations").json()[0]["has_open_appeal"] is True
    assert len(client.get("/api/appeals?status=open").json()) == 1
    invalid = client.post(f"/api/appeals/{aid}/decision", json={"decision": "change", "final_answer": "na", "reason": "Причина"})
    assert invalid.status_code == 422
    body = {"decision": "change", "final_answer": "no", "reason": "Проверяющий отменил оценку контакта."}
    decided = client.post(f"/api/appeals/{aid}/decision", json=body)
    assert decided.status_code == 200, decided.text
    assert decided.json()["before_total"] == "100.00" and decided.json()["after_total"] == "90.00"
    assert client.post(f"/api/appeals/{aid}/decision", json=body).status_code == 409
    result = client.get(f"/api/evaluations/{eid}").json()
    assert result["original"] == evaluation["original"]
    assert result["total"] == "90.00" and result["original_total"] == "100.00"
    assert client.get("/api/conversations").json()[0]["has_open_appeal"] is False


def test_ai_suggestion_is_only_advice_and_includes_usage(api, monkeypatch):
    client, _, _ = api
    upload(client)
    evaluation = client.post("/api/evaluations", json={"conversation_id": "test-1"}).json()
    created = client.post("/api/appeals", json={"evaluation_id": evaluation["evaluation_id"], "criterion_id": "01", "comment": "Перепроверить."}).json()

    def recommend(*args, **kwargs):
        payload = {"criterion_id": "01", "original_answer": "yes", "decision": "confirm", "final_answer": "yes",
                   "reason": "Приветствие подтверждено.", "evidence": [{"message_id": "m2", "quote": "Здравствуйте, меня зовут Анна."}]}
        return {"content": json.dumps(payload), "model": "test-model", "finish_reason": "stop", "http_status": 200,
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    monkeypatch.setattr(llm_client, "request_completion", recommend)
    response = client.post(f"/api/appeals/{created['appeal_id']}/ai-suggest", json={})
    assert response.status_code == 200, response.text
    assert response.json()["is_suggestion"] is True
    assert response.json()["suggestion"]["evidence"][0]["speaker"] == "operator"
    updated = client.get(f"/api/evaluations/{evaluation['evaluation_id']}").json()
    assert updated["original"] == evaluation["original"] and updated["final"] == evaluation["final"]
    assert updated["decisions"] == [] and updated["usage"]["total_tokens"] == 70


def test_partial_batch_prompt_subset_and_weight_only_zero_calls(api):
    client, _, calls = api
    upload(client)
    original = client.post("/api/evaluations", json={"conversation_id": "test-1"}).json()
    checklist = client.get("/api/checklists/active").json()
    checklist["criteria"][4]["description"] = "Учитывай реакцию на конкретное неудобство клиента."
    version = client.post("/api/checklists", json=checklist).json()
    assert version["change_summary"]["evaluation_required"] == ["05"]
    batch = client.post("/api/evaluations/batch", json={"all": True, "criterion_ids": ["05"]}).json()
    job, _ = wait_job(client, batch["job_id"])
    assert not job["errors"]
    assert len(calls) == 2
    sent_checklist = json.loads(calls[-1].splitlines()[3])
    assert [row["criterion_id"] for row in sent_checklist["criteria"]] == ["05"]
    current = client.get("/api/conversations/test-1").json()["latest_result"]
    assert current["total"] == original["total"]
    version["criteria"][0]["weight"] = 12
    changed_weight = client.post("/api/checklists", json=version).json()
    assert changed_weight["change_summary"]["evaluation_required"] == []
    recalc = client.post("/api/evaluations/batch", json={"all": True, "criterion_ids": []}).json()
    job, _ = wait_job(client, recalc["job_id"])
    assert not job["errors"] and len(calls) == 2


def test_upload_limit_before_parsing_with_and_without_content_length(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_DATASET_BYTES", "1024")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    with TestClient(create_app(tmp_path / "limited.sqlite3")) as client:
        for payload in (b"x" * 1025, iter([b"x" * 600, b"y" * 600])):
            result = client.post("/api/conversations/upload", content=payload,
                                 headers={"Content-Type": "application/json", "Origin": "http://localhost:3000"})
            assert result.status_code == 413
            assert result.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
            assert result.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert client.get("/api/conversations").json() == []
        empty_id = client.post("/api/conversations/upload?conversation_id=", content="operator: hello", headers={"Content-Type": "text/plain"})
        assert empty_id.status_code == 422
