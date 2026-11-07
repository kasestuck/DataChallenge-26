"""Only tests substitute HTTP transports; no real model calls."""

import json
import ssl
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime

import httpx
import pytest
from openai import OpenAI

from backend import llm_client, meta_client
from backend.checklist import get_checklist
from backend.config import ConfigurationError, LLMOptions, MetaSettings, load_llm_options
from backend.db import connect_db, init_db
from backend.llm_client import LLMClient, summarize_calls
from backend.meta_client import ModelRequestError

SECRET = "test-secret-never-log"
SETTINGS = MetaSettings(SECRET, "https://model.example/v1", "test-model")


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "calls.db"
    init_db(path)
    c = connect_db(path)
    version = get_checklist(c)["checklist_version_id"]
    with c:
        c.execute("""INSERT INTO conversations(conversation_id,channel,language,raw_json,content_hash,created_at,updated_at)
                  VALUES ('chat','agent chat','RU','{}','test','now','now')""")
        c.execute("""INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,
                  conversation_snapshot_json,status,created_at,updated_at)
                  VALUES ('eval','chat',?,1,'{}','running','now','now')""", (version,))
    yield c
    c.close()


def body(content='{"criteria":[]}', finish_reason="stop", usage=True):
    return {
        "id": "resp", "object": "chat.completion", "created": 1, "model": "returned-model",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30} if usage else None,
    }


def transport(monkeypatch, handler):
    calls, settings, sleeps = [], [], []

    def handle(request):
        calls.append(json.loads(request.content))
        return handler(request, len(calls))

    def factory(**kwargs):
        settings.append(kwargs)
        system_client = kwargs.pop("http_client", None)
        if system_client is not None:
            system_client.close()
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(meta_client, "OpenAI", factory)
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_client.random, "uniform", lambda low, high: 0)
    return calls, settings, sleeps


def rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM llm_calls ORDER BY attempt")]


def test_success_logged_with_exact_usage_cost_and_options(conn, monkeypatch):
    calls, client_settings, sleeps = transport(monkeypatch, lambda r, n: httpx.Response(200, json=body()))
    options = LLMOptions(max_tokens=9000, timeout_seconds=80,
                         price_input_per_1m=Decimal("2"), price_output_per_1m=Decimal("4"))
    result = LLMClient(conn, SETTINGS, options).complete("system", "user", evaluation_id="eval")
    assert result["model"] == "returned-model"
    assert result["finish_reason"] == "stop"
    assert calls[0]["max_tokens"] == 9000
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert client_settings[0]["timeout"] == 80
    assert client_settings[0]["max_retries"] == 0
    assert sleeps == []
    record = rows(conn)[0]
    assert record["raw_response"] == '{"criteria":[]}'
    assert record["purpose"] == "evaluation"
    assert record["attempt"] == 1
    assert record["prompt_tokens"] == 10 and record["completion_tokens"] == 20
    assert record["http_status"] == 200 and record["error_code"] is None
    assert Decimal(record["cost"]) == Decimal("0.0001")
    summary = summarize_calls(conn, "eval")
    assert summary["total_tokens"] == 30 and summary["usage_complete"] is True
    assert Decimal(summary["cost"]) == Decimal("0.0001")


@pytest.mark.parametrize("status,attempts", [(401, 1), (403, 1), (404, 1), (400, 1), (429, 4), (500, 3), (503, 3)])
def test_status_retry_limits_and_safe_error_logging(conn, monkeypatch, status, attempts):
    calls, _, sleeps = transport(monkeypatch, lambda r, n: httpx.Response(status, json={"error": {"message": SECRET}}))
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions()).complete("system", "user", evaluation_id="eval")
    assert len(calls) == attempts
    assert len(rows(conn)) == attempts
    assert len(sleeps) == attempts - 1
    assert [r["attempt"] for r in rows(conn)] == list(range(1, attempts + 1))
    assert SECRET not in str(error.value)
    assert all(r["raw_response"] is None and r["http_status"] == status for r in rows(conn))
    assert all(r["prompt_tokens"] is None and r["cost"] is None for r in rows(conn))
    if status == 404:
        assert "test-model" in str(error.value)


def test_timeout_three_attempts(conn, monkeypatch):
    def fail(request, n):
        raise httpx.ReadTimeout(SECRET, request=request)

    calls, _, _ = transport(monkeypatch, fail)
    with pytest.raises(ModelRequestError, match="Таймаут"):
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert len(calls) == 3
    assert [r["error_code"] for r in rows(conn)] == ["MODEL_TIMEOUT"] * 3


def test_connection_error_not_retried(conn, monkeypatch):
    def fail(request, n):
        raise httpx.ConnectError(SECRET, request=request)

    calls, _, _ = transport(monkeypatch, fail)
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert error.value.code == "MODEL_CONNECTION_ERROR"
    assert len(calls) == len(rows(conn)) == 1


def test_retry_after_honored_and_attempts_recorded(conn, monkeypatch):
    def handler(request, n):
        return httpx.Response(429, json={"error": {}}, headers={"retry-after": "7"}) if n == 1 else httpx.Response(200, json=body())

    calls, _, sleeps = transport(monkeypatch, handler)
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert sleeps == [7]
    assert len(calls) == 2
    assert [r["purpose"] for r in rows(conn)] == ["evaluation", "retry"]


def test_retry_after_http_date():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=15), usegmt=True)
    assert 13 <= meta_client._retry_after(future) <= 15
    assert meta_client._retry_after("garbage") is None
    assert meta_client._retry_after("NaN") is None
    assert meta_client._retry_after("-2") == 0


def test_appeal_retry_has_explicit_retry_purpose(conn, monkeypatch):
    transport(monkeypatch, lambda r, n: httpx.Response(503, json={"error": {}})
              if n == 1 else httpx.Response(200, json=body()))
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval", purpose="appeal")
    assert [r["purpose"] for r in rows(conn)] == ["appeal", "retry"]


def test_long_retry_after_split_into_short_waits(conn, monkeypatch):
    transport(monkeypatch, lambda r, n: httpx.Response(429, json={"error": {}}, headers={"retry-after": "65"}) if n == 1 else httpx.Response(200, json=body()))
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert sleeps == [30, 30, 5]


@pytest.mark.parametrize("detail", [
    {"param": "response_format", "message": SECRET},
    {"message": "response_format is not supported"},
    {"message": "Unknown parameter: response_format"},
])
def test_json_mode_fallback_persisted_and_every_call_logged(conn, monkeypatch, detail):
    def handler(request, n):
        if n == 1:
            return httpx.Response(400, json={"error": detail})
        return httpx.Response(200, json=body())

    calls, _, _ = transport(monkeypatch, handler)
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval", purpose="retry")
    assert len(calls) == 3
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1] and "response_format" not in calls[2]
    assert [r["attempt"] for r in rows(conn)] == [1, 2, 3]
    assert [r["response_format_used"] for r in rows(conn)] == [1, 0, 0]
    assert summarize_calls(conn, "eval")["total_tokens"] == 60


def test_unrelated_400_does_not_disable_json_mode(conn, monkeypatch):
    calls, _, _ = transport(monkeypatch, lambda r, n: httpx.Response(400, json={"error": {"param": "max_tokens", "message": "Bad limit"}}))
    with pytest.raises(ModelRequestError):
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert len(calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE 'llm_json_mode_unsupported:%'").fetchone()[0] == 0


def test_capability_cache_separated_by_model(conn, monkeypatch):
    def handler(request, n):
        if n == 1:
            return httpx.Response(400, json={"error": {"param": "response_format"}})
        return httpx.Response(200, json=body())

    calls, _, _ = transport(monkeypatch, handler)
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    other = MetaSettings(SECRET, SETTINGS.base_url, "other-model")
    LLMClient(conn, other, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert "response_format" in calls[2]


def test_truncated_response_is_error_but_raw_and_usage_are_preserved(conn, monkeypatch):
    calls, _, _ = transport(monkeypatch, lambda r, n: httpx.Response(200, json=body('{"incomplete":', "length")))
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert error.value.code == "MODEL_OUTPUT_TRUNCATED"
    assert len(calls) == 1
    record = rows(conn)[0]
    assert record["finish_reason"] == "length"
    assert record["raw_response"] == '{"incomplete":'
    assert record["total_tokens"] == 30


@pytest.mark.parametrize("finish_reason", [None, "content_filter", "tool_calls"])
def test_non_stop_response_not_success(conn, monkeypatch, finish_reason):
    transport(monkeypatch, lambda r, n: httpx.Response(200, json=body(finish_reason=finish_reason)))
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert error.value.code == "MODEL_INCOMPLETE_RESPONSE"
    assert len(rows(conn)) == 1


def test_missing_usage_does_not_invent_tokens_or_cost(conn, monkeypatch):
    transport(monkeypatch, lambda r, n: httpx.Response(200, json=body(usage=False)))
    options = LLMOptions(price_input_per_1m=Decimal(2), price_output_per_1m=Decimal(3))
    LLMClient(conn, SETTINGS, options).complete("s", "u", evaluation_id="eval")
    summary = summarize_calls(conn, "eval")
    assert summary["total_tokens"] is None
    assert summary["cost"] is None and summary["usage_complete"] is False
    assert summary["price_status"] == "configured"


def test_validation_retry_sums_all_usage_and_cost(conn, monkeypatch):
    transport(monkeypatch, lambda r, n: httpx.Response(200, json=body()))
    client = LLMClient(conn, SETTINGS, LLMOptions(price_input_per_1m=Decimal(1), price_output_per_1m=Decimal(2)))
    client.complete("s", "u", evaluation_id="eval")
    client.complete("s", "correct errors", evaluation_id="eval", purpose="retry")
    summary = summarize_calls(conn, "eval")
    assert summary["calls"] == 2
    assert summary["prompt_tokens"] == 20 and summary["completion_tokens"] == 40
    assert Decimal(summary["cost"]) == Decimal("0.0001")
    assert [r["attempt"] for r in rows(conn)] == [1, 2]


def test_missing_prices_do_not_invent_cost(conn, monkeypatch):
    transport(monkeypatch, lambda r, n: httpx.Response(200, json=body()))
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    summary = summarize_calls(conn, "eval")
    assert summary["total_tokens"] == 30
    assert summary["cost"] is None and summary["price_status"] == "price_not_set"


def test_oversized_input_is_not_sent_or_silently_truncated(conn, monkeypatch):
    calls, _, _ = transport(monkeypatch, lambda r, n: pytest.fail("Request forbidden"))
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions(max_input_chars=3)).complete("long", "input", evaluation_id="eval")
    assert error.value.code == "MODEL_INPUT_TOO_LARGE"
    assert calls == [] and rows(conn) == []


def test_unexpected_sdk_error_recorded_safely(conn, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(meta_client, "OpenAI", fail)
    with pytest.raises(ModelRequestError) as error:
        LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    assert error.value.code == "MODEL_SDK_ERROR"
    assert SECRET not in str(error.value)
    assert rows(conn)[0]["error_code"] == "MODEL_SDK_ERROR"


def test_options_parse_without_model_key(monkeypatch, tmp_path):
    for key in ("LLM_MAX_TOKENS", "LLM_MAX_INPUT_CHARS", "LLM_TIMEOUT_SECONDS", "PRICE_INPUT_PER_1M", "PRICE_OUTPUT_PER_1M", "LLM_USE_SYSTEM_TRUST"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text("LLM_MAX_TOKENS=9000\nPRICE_INPUT_PER_1M=0.25\nPRICE_OUTPUT_PER_1M=\n", encoding="utf-8")
    options = load_llm_options(path)
    assert options.max_tokens == 9000
    assert options.price_input_per_1m == Decimal("0.25")
    assert options.price_output_per_1m is None
    assert options.use_system_trust is True


@pytest.mark.parametrize("key,value", [("LLM_MAX_TOKENS", "0"), ("LLM_TIMEOUT_SECONDS", "-1"), ("PRICE_INPUT_PER_1M", "NaN"), ("PRICE_OUTPUT_PER_1M", "-2"), ("LLM_USE_SYSTEM_TRUST", "maybe"), ("LLM_USE_SYSTEM_TRUST", "1")])
def test_bad_options_rejected(monkeypatch, tmp_path, key, value):
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError):
        load_llm_options(path)


def test_system_trust_can_be_disabled_explicitly(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("LLM_USE_SYSTEM_TRUST=false\n", encoding="utf-8")
    monkeypatch.delenv("LLM_USE_SYSTEM_TRUST", raising=False)
    assert load_llm_options(path).use_system_trust is False


def test_system_trust_keeps_certificate_and_hostname_verification(monkeypatch):
    captured = {}

    class HTTPStub:
        closed = False

        def close(self):
            self.closed = True

    http_stub = HTTPStub()
    sdk_stub = object()

    def http_factory(**kwargs):
        captured.update(kwargs)
        return http_stub

    def sdk_factory(**kwargs):
        assert kwargs["http_client"] is http_stub
        return sdk_stub

    monkeypatch.setattr(meta_client.httpx, "Client", http_factory)
    monkeypatch.setattr(meta_client, "OpenAI", sdk_factory)
    assert meta_client._create_client(SETTINGS, timeout=120, use_system_trust=True) is sdk_stub
    context = captured["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert captured["timeout"] == 120
    assert http_stub.closed is False


def test_llm_client_passes_system_trust_option(conn, monkeypatch):
    calls = []

    def request(system, user, **kwargs):
        calls.append(kwargs)
        return {"content": "{}", "model": "test-model", "finish_reason": "stop", "usage": None, "http_status": 200}

    monkeypatch.setattr(llm_client, "request_completion", request)
    LLMClient(conn, SETTINGS, LLMOptions()).complete("s", "u", evaluation_id="eval")
    LLMClient(conn, SETTINGS, LLMOptions(use_system_trust=False)).complete("s", "u", evaluation_id="eval")
    assert [c["use_system_trust"] for c in calls] == [True, False]
