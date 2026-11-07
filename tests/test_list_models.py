"""Model discovery tests use an in-memory HTTP transport, never the real service."""

import httpx
import pytest
from openai import OpenAI

from backend import meta_client
from scripts import list_models

KEY = "test-list-secret-never-print"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name in ("MODEL_API_KEY", "META_BASE_URL", "META_MODEL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    # A missing model must not prevent discovery of the available model IDs.
    path.write_text(
        f"MODEL_API_KEY={KEY}\nMETA_BASE_URL=https://model.example/v1\n",
        encoding="utf-8",
    )
    return path


def install_transport(monkeypatch, handler):
    calls, options = [], []

    def handle(request):
        calls.append(request)
        return handler(request)

    def factory(**kwargs):
        options.append(kwargs)
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(meta_client, "OpenAI", factory)
    return calls, options


def model_payload(*ids):
    return {
        "object": "list",
        "data": [{"id": model_id, "object": "model", "created": 1, "owned_by": "provider"} for model_id in ids],
    }


def test_model_ids_without_configured_model(monkeypatch, configured, capsys):
    calls, options = install_transport(
        monkeypatch, lambda request: httpx.Response(200, json=model_payload("model-a", "model-b"))
    )
    assert list_models.main(["--env-file", str(configured)]) == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == ["model-a", "model-b"]
    assert output.err == ""
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://model.example/v1/models"
    assert options[0]["timeout"] == 60
    assert options[0]["max_retries"] == 0


@pytest.mark.parametrize("status,code", [
    (401, "MODEL_ACCESS_DENIED"), (403, "MODEL_ACCESS_DENIED"),
    (404, "MODELS_ENDPOINT_UNAVAILABLE"), (405, "MODELS_ENDPOINT_UNAVAILABLE"),
    (501, "MODELS_ENDPOINT_UNAVAILABLE"), (429, "MODEL_RATE_LIMITED"),
    (500, "MODEL_UNAVAILABLE"), (503, "MODEL_UNAVAILABLE"),
])
def test_errors_are_safe_and_not_retried(monkeypatch, configured, capsys, status, code):
    calls, _ = install_transport(
        monkeypatch, lambda request: httpx.Response(status, json={"error": {"message": KEY}})
    )
    assert list_models.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert code in output.err
    assert output.out == ""
    assert KEY not in output.err
    assert len(calls) == 1
    if status in {404, 405, 501}:
        assert "не поддерживается" in output.err


@pytest.mark.parametrize("exc_type,code", [
    (httpx.ReadTimeout, "MODEL_TIMEOUT"), (httpx.ConnectError, "MODEL_CONNECTION_ERROR"),
])
def test_network_errors(monkeypatch, configured, capsys, exc_type, code):
    def fail(request):
        raise exc_type(KEY, request=request)

    calls, _ = install_transport(monkeypatch, fail)
    assert list_models.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert code in output.err
    assert KEY not in output.err + output.out
    assert len(calls) == 1


def test_empty_list_does_not_invent_ids(monkeypatch, configured, capsys):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=model_payload()))
    assert list_models.main(["--env-file", str(configured)]) == 0
    assert "пустой список" in capsys.readouterr().out


@pytest.mark.parametrize("body", [
    {}, {"data": None}, {"data": [{}]}, {"data": [{"id": ""}]},
    {"data": [{"id": None}]}, {"data": [{"id": "bad\nline"}]},
])
def test_invalid_list_is_error(monkeypatch, configured, capsys, body):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    assert list_models.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert "MODEL_INVALID_RESPONSE" in output.err
    assert not output.out


def test_known_partial_list_is_not_reported_as_complete(monkeypatch, configured, capsys):
    body = {**model_payload("model-a"), "has_more": True}
    calls, _ = install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    assert list_models.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert "MODEL_LIST_INCOMPLETE" in output.err
    assert not output.out
    assert len(calls) == 1


def test_missing_key_prevents_network(monkeypatch, configured, capsys):
    monkeypatch.setenv("MODEL_API_KEY", "")
    calls, _ = install_transport(monkeypatch, lambda request: pytest.fail("No request expected"))
    assert list_models.main(["--env-file", str(configured)]) == 2
    assert "MODEL_API_KEY" in capsys.readouterr().err
    assert calls == []


def test_secret_returned_in_model_id_is_redacted(monkeypatch, configured, capsys):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=model_payload(KEY)))
    assert list_models.main(["--env-file", str(configured)]) == 0
    assert capsys.readouterr().out.strip() == "[REDACTED]"


def test_unexpected_error_is_safe(monkeypatch, configured, capsys):
    def fail(**kwargs):
        raise RuntimeError(KEY)

    monkeypatch.setattr(meta_client, "OpenAI", fail)
    assert list_models.main(["--env-file", str(configured)]) == 1
    output = capsys.readouterr()
    assert "UNEXPECTED_ERROR" in output.err
    assert KEY not in output.out + output.err
