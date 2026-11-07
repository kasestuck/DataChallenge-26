"""OpenAI-compatible requests. No retries or fallback models in diagnostic tools."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
import ssl
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from backend.config import MetaSettings, load_settings


class ModelRequestError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int | None = None,
                 *, retry_after: float | None = None, response_format_unsupported: bool = False,
                 completion: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after
        self.response_format_unsupported = response_format_unsupported
        self.completion = completion


def _http_error(status: int, model: str, *, listing: bool = False) -> ModelRequestError:
    if listing and status in {404, 405, 501}:
        return ModelRequestError(
            "MODELS_ENDPOINT_UNAVAILABLE",
            f"HTTP {status}: /models недоступен или не поддерживается провайдером. "
            "Список моделей получить не удалось; проверьте META_BASE_URL и документацию провайдера.",
            status,
        )
    if status in {401, 403}:
        code = "MODEL_ACCESS_DENIED"
        message = f"HTTP {status}: проверьте ключ и доступ к API/модели."
    elif status == 404:
        code = "MODEL_NOT_FOUND"
        message = (
            f"HTTP 404: проверьте доступность модели '{model}' "
            "и правильность META_BASE_URL. Модель или маршрут API не найдены."
        )
    elif status == 429:
        code = "MODEL_RATE_LIMITED"
        message = "HTTP 429: ограничение частоты или квоты. Повторите запрос позже."
    elif status >= 500:
        code = "MODEL_UNAVAILABLE"
        message = f"HTTP {status}: ошибка сервиса модели. Повторите запрос позже."
    else:
        code = "MODEL_HTTP_ERROR"
        message = f"HTTP {status}: API отклонил запрос. Проверьте совместимость endpoint и модели."
    return ModelRequestError(code, message, status)


def _create_client(settings: MetaSettings, *, timeout: float = 60,
                   use_system_trust: bool = False) -> OpenAI:
    kwargs: dict[str, Any] = {
        "api_key": settings.api_key, "base_url": settings.base_url,
        "timeout": timeout, "max_retries": 0,
    }
    http_client = None
    if use_system_trust:
        # System-trusted corporate/antivirus roots are available on Windows.
        # The default context still requires a valid certificate and hostname.
        http_client = httpx.Client(verify=ssl.create_default_context(), timeout=timeout)
        kwargs["http_client"] = http_client
    try:
        return OpenAI(**kwargs)
    except Exception:
        if http_client is not None:
            http_client.close()
        raise


def ask_meta(
    system_prompt: str,
    user_prompt: str,
    *,
    settings: MetaSettings | None = None,
) -> dict[str, Any]:
    """Return actual content/model/usage or raise; never generate a fake result."""
    settings = settings or load_settings()
    try:
        with _create_client(settings) as client:
            response = client.chat.completions.create(
                model=settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
    except APITimeoutError:
        raise ModelRequestError("MODEL_TIMEOUT", "Таймаут запроса к модели (лимит 60 секунд).") from None
    except APIConnectionError:
        raise ModelRequestError(
            "MODEL_CONNECTION_ERROR", "Не удалось подключиться к API. Проверьте сеть и META_BASE_URL."
        ) from None
    except APIStatusError as exc:
        # Do not print provider bodies, headers, URLs or the original exception.
        safe_model = settings.model.replace(settings.api_key, "[REDACTED]")
        raise _http_error(exc.status_code, safe_model) from None

    try:
        content = response.choices[0].message.content
        model = response.model
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty content")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Missing returned model")
        usage = None
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
    except (AttributeError, IndexError, TypeError, ValueError):
        raise ModelRequestError(
            "MODEL_INVALID_RESPONSE",
            "API вернул неожиданный или пустой ответ. Smoke test не пройден.",
        ) from None
    return {"content": content, "model": model, "usage": usage}


def list_meta_models(*, settings: MetaSettings | None = None) -> list[str]:
    """Read model IDs from /models; never guess IDs or silently fall back."""
    settings = settings or load_settings(require_model=False)
    try:
        with _create_client(settings) as client:
            # models.list() returns a SyncPage. Reading .data deliberately avoids
            # implicit iteration or any accidental unbounded pagination.
            response = client.models.list()
    except APITimeoutError:
        raise ModelRequestError(
            "MODEL_TIMEOUT", "Таймаут запроса списка моделей (лимит 60 секунд)."
        ) from None
    except APIConnectionError:
        raise ModelRequestError(
            "MODEL_CONNECTION_ERROR", "Не удалось подключиться к API. Проверьте сеть и META_BASE_URL."
        ) from None
    except APIStatusError as exc:
        raise _http_error(exc.status_code, "", listing=True) from None

    try:
        data = response.data
        if not isinstance(data, list):
            raise ValueError("Missing models data")
        model_ids = []
        for model in data:
            model_id = model.id
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("Invalid model ID")
            if any(ord(char) < 32 for char in model_id):
                raise ValueError("Control characters in model ID")
            model_ids.append(model_id)
        # A provider may add pagination metadata to this nominally unpaginated
        # endpoint. Do not present a known partial list as the complete list.
        if response.has_next_page() or getattr(response, "has_more", False) is True:
            raise ModelRequestError(
                "MODEL_LIST_INCOMPLETE",
                "API вернул только часть списка моделей. Пагинация этого провайдера "
                "пока не поддерживается; полный список не подтверждён.",
            )
    except (AttributeError, TypeError, ValueError):
        raise ModelRequestError(
            "MODEL_INVALID_RESPONSE", "API /models вернул некорректный список моделей."
        ) from None
    return model_ids


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if isfinite(seconds) else None


def _format_rejected(exc: APIStatusError) -> bool:
    """Inspect only to classify the parameter; never expose the provider body."""
    if exc.status_code not in (400, 422):
        return False
    body = exc.body
    if not isinstance(body, dict):
        return False
    detail = body.get("error", body)
    if not isinstance(detail, dict):
        return False
    param = str(detail.get("param") or "").lower()
    message = str(detail.get("message") or "").lower()
    if param == "response_format" or param.startswith("response_format."):
        return True
    mentions_format = "response_format" in message
    rejects_parameter = any(word in message for word in (
        "not supported", "unsupported", "unknown parameter", "unrecognized", "not permitted",
        "unexpected parameter", "not allowed", "invalid parameter",
    ))
    return mentions_format and rejects_parameter


def request_completion(system_prompt: str, user_prompt: str, *, settings: MetaSettings,
                       max_tokens: int, json_mode: bool, timeout_seconds: float = 120,
                       use_system_trust: bool = False) -> dict[str, Any]:
    """One physical request, with metadata preserved even for unusable completions."""
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        with _create_client(settings, timeout=timeout_seconds, use_system_trust=use_system_trust) as client:
            response = client.chat.completions.create(**kwargs)
    except APITimeoutError:
        raise ModelRequestError("MODEL_TIMEOUT", f"Таймаут запроса к модели ({timeout_seconds:g} секунд).") from None
    except APIConnectionError:
        raise ModelRequestError("MODEL_CONNECTION_ERROR", "Не удалось подключиться к API модели.") from None
    except APIStatusError as exc:
        error = _http_error(exc.status_code, settings.model.replace(settings.api_key, "[REDACTED]"))
        error.retry_after = _retry_after(exc.response.headers.get("retry-after"))
        error.response_format_unsupported = bool(json_mode and _format_rejected(exc))
        raise error from None
    except Exception:
        raise ModelRequestError("MODEL_SDK_ERROR", "Неожиданная ошибка SDK; ответ модели не получен.") from None

    usage = None
    usage_obj = getattr(response, "usage", None)
    if usage_obj is not None:
        usage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage_obj, key, None)
            usage[key] = value if type(value) is int and value >= 0 else None
    model = getattr(response, "model", None)
    content, finish_reason = None, None
    try:
        choice = response.choices[0]
        content = choice.message.content
        finish_reason = choice.finish_reason
    except (AttributeError, IndexError, TypeError):
        pass
    result = {
        "content": content if isinstance(content, str) else None,
        "model": model if isinstance(model, str) and model.strip() else settings.model,
        "usage": usage,
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "http_status": 200,
    }
    if finish_reason == "length":
        raise ModelRequestError(
            "MODEL_OUTPUT_TRUNCATED", "Ответ модели обрезан по лимиту токенов; оценка не принята.",
            200, completion=result,
        )
    if finish_reason != "stop":
        raise ModelRequestError(
            "MODEL_INCOMPLETE_RESPONSE", "Ответ модели не завершён обычным finish_reason=stop; оценка не принята.",
            200, completion=result,
        )
    if not isinstance(content, str) or not content.strip() or not isinstance(model, str) or not model.strip():
        raise ModelRequestError("MODEL_INVALID_RESPONSE", "API вернул пустой или некорректный ответ.", 200, completion=result)
    return result
