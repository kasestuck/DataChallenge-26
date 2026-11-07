"""Transport is mocked only here; production code always calls the real API."""

import json

import httpx
import pytest
from openai import OpenAI

from backend import meta_client
from backend.config import ConfigurationError, MetaSettings, load_settings
from scripts import smoke_test

KEY = "test-secret-not-for-logs"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name in ("MODEL_API_KEY", "META_BASE_URL", "META_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODEL_API_KEY={KEY}\nMETA_BASE_URL=https://model.example/v1\nMETA_MODEL=test-model\n",
        encoding="utf-8",
    )
    return env_file


def install_transport(monkeypatch, handler):
    requests = []
    options = []

    def handle(request):
        requests.append(request)
        return handler(request)

    def factory(**kwargs):
        options.append(kwargs)
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(meta_client, "OpenAI", factory)
    return requests, options


def response_body(**overrides):
    return {
        "id": "test-response",
        "object": "chat.completion",
        "created": 1,
        "model": "actual-returned-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "CQA smoke test OK"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        **overrides,
    }


def test_success_one_request_and_actual_usage(monkeypatch, configured, capsys):
    requests, options = install_transport(monkeypatch, lambda r: httpx.Response(200, json=response_body()))
    assert smoke_test.main(["--env-file", str(configured)]) == 0
    output = capsys.readouterr()
    assert '"model": "actual-returned-model"' in output.out
    assert '"total_tokens": 17' in output.out
    assert KEY not in output.out + output.err
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "test-model"
    assert options[0]["max_retries"] == 0
    assert options[0]["timeout"] == 60


@pytest.mark.parametrize("status,code", [
    (401, "MODEL_ACCESS_DENIED"), (403, "MODEL_ACCESS_DENIED"),
    (404, "MODEL_NOT_FOUND"), (429, "MODEL_RATE_LIMITED"),
    (500, "MODEL_UNAVAILABLE"), (503, "MODEL_UNAVAILABLE"),
    (400, "MODEL_HTTP_ERROR"),
])
def test_http_failures_no_retries_no_secrets(monkeypatch, configured, capsys, status, code):
    requests, _ = install_transport(
        monkeypatch, lambda r: httpx.Response(status, json={"error": {"message": KEY}})
    )
    assert smoke_test.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert code in output.err
    assert KEY not in output.err + output.out
    assert not output.out
    assert len(requests) == 1
    if status == 404:
        assert "test-model" in output.err


@pytest.mark.parametrize("exc_type,code", [
    (httpx.ReadTimeout, "MODEL_TIMEOUT"), (httpx.ConnectError, "MODEL_CONNECTION_ERROR"),
])
def test_network_failures(monkeypatch, configured, capsys, exc_type, code):
    def fail(request):
        raise exc_type(KEY, request=request)

    requests, _ = install_transport(monkeypatch, fail)
    assert smoke_test.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert code in output.err
    assert KEY not in output.err + output.out
    assert len(requests) == 1


def test_missing_usage_is_not_zero(monkeypatch, configured, capsys):
    install_transport(monkeypatch, lambda r: httpx.Response(200, json=response_body(usage=None)))
    assert smoke_test.main(["--env-file", str(configured)]) == 0
    output = capsys.readouterr().out
    assert '"usage": null' in output
    assert "не предоставил" in output


@pytest.mark.parametrize("body", [
    response_body(choices=[]), response_body(model=None),
    response_body(choices=[{"message": {"content": None}}]),
    response_body(choices=[{"message": {"content": "  "}}]),
    {"unexpected": KEY},
])
def test_invalid_response_is_not_success(monkeypatch, configured, capsys, body):
    install_transport(monkeypatch, lambda r: httpx.Response(200, json=body))
    assert smoke_test.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert "MODEL_INVALID_RESPONSE" in output.err
    assert KEY not in output.err + output.out


def test_missing_key_does_not_call_api(monkeypatch, configured, capsys):
    monkeypatch.setenv("MODEL_API_KEY", "")
    requests, _ = install_transport(monkeypatch, lambda r: pytest.fail("Must not send a request"))
    assert smoke_test.main(["--env-file", str(configured)]) == 2
    assert "MODEL_API_KEY" in capsys.readouterr().err
    assert requests == []


def test_explicit_missing_env_file(configured, tmp_path):
    with pytest.raises(ConfigurationError, match="не найден"):
        load_settings(tmp_path / "missing.env")


def test_process_environment_takes_precedence(monkeypatch, configured):
    monkeypatch.setenv("META_MODEL", "override-model")
    settings = load_settings(configured)
    assert settings.model == "override-model"
    assert KEY not in repr(settings)


def test_provider_echo_of_secret_is_redacted(monkeypatch, configured, capsys):
    body = response_body(choices=[{"message": {"content": KEY}}])
    install_transport(monkeypatch, lambda r: httpx.Response(200, json=body))
    assert smoke_test.main(["--env-file", str(configured)]) == 0
    output = capsys.readouterr().out
    assert KEY not in output
    assert "[REDACTED]" in output


def test_unexpected_exception_does_not_expose_secrets(monkeypatch, configured, capsys):
    def fail(**kwargs):
        raise RuntimeError(KEY)

    monkeypatch.setattr(meta_client, "OpenAI", fail)
    assert smoke_test.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert "UNEXPECTED_ERROR" in output.err
    assert KEY not in output.err + output.out
