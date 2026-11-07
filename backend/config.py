"""Independent model and dataset settings; stage 1 does not require an AI key."""

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """A configuration error whose message is safe to show to the user."""


@dataclass(frozen=True)
class MetaSettings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.meta.ai/v1"
    model: str = "muse-spark-1.3"


def read_environment(env_file: Path | None = None) -> dict:
    path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    if env_file is not None and not path.is_file():
        raise ConfigurationError("Указанный файл .env не найден.")
    return {**dotenv_values(path, encoding="utf-8-sig"), **os.environ}


def load_settings(env_file: Path | None = None, *, require_model: bool = True) -> MetaSettings:
    values = read_environment(env_file)
    key = (values.get("MODEL_API_KEY") or "").strip()
    if not key or key in {"your-api-key", "replace-me", "YOUR_API_KEY"}:
        raise ConfigurationError(
            "MODEL_API_KEY не задан. Вставьте ключ в локальный .env "
            "или задайте переменную окружения MODEL_API_KEY."
        )
    base_url = (values.get("META_BASE_URL") or "").strip()
    model = (values.get("META_MODEL") or "").strip()
    if not base_url:
        raise ConfigurationError("META_BASE_URL не задан; заполните его по .env.example.")
    if require_model and not model:
        raise ConfigurationError("META_MODEL не задан; заполните имя модели в .env.")
    try:
        parsed = urlsplit(base_url)
        valid_url = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
        _ = parsed.port
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ConfigurationError(
            "META_BASE_URL должен быть HTTP(S)-адресом без ключей, query-параметров и fragment."
        )
    return MetaSettings(api_key=key, base_url=base_url, model=model)


@dataclass(frozen=True)
class DataSettings:
    database_path: Path
    api_url: str = field(repr=False)
    group_id: str = "DEV"
    max_dataset_bytes: int = 20 * 1024 * 1024


def load_data_settings(env_file: Path | None = None) -> DataSettings:
    values = read_environment(env_file)
    path = Path(values.get("DATABASE_PATH") or "data/cqa.db").expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    group_id = (values.get("DATASET_GROUP_ID") or "DEV").strip()
    if not group_id:
        raise ConfigurationError("DATASET_GROUP_ID не должен быть пустым.")
    try:
        limit = int(values.get("MAX_DATASET_BYTES") or 20 * 1024 * 1024)
    except (TypeError, ValueError):
        raise ConfigurationError("MAX_DATASET_BYTES должен быть положительным целым числом.") from None
    if limit <= 0:
        raise ConfigurationError("MAX_DATASET_BYTES должен быть положительным целым числом.")
    return DataSettings(path.resolve(), (values.get("DATASET_API_URL") or "").strip(), group_id, limit)


@dataclass(frozen=True)
class LLMOptions:
    max_tokens: int = 8192
    max_input_chars: int = 100000
    timeout_seconds: float = 120
    price_input_per_1m: Decimal | None = None
    price_output_per_1m: Decimal | None = None
    use_system_trust: bool = True


def load_llm_options(env_file: Path | None = None) -> LLMOptions:
    values = read_environment(env_file)

    def integer(name: str, default: int) -> int:
        try:
            value = int(values.get(name) or default)
        except (TypeError, ValueError):
            raise ConfigurationError(f"{name} должен быть положительным целым числом.") from None
        if value <= 0:
            raise ConfigurationError(f"{name} должен быть положительным целым числом.")
        return value

    def price(name: str) -> Decimal | None:
        raw = values.get(name)
        if raw is None or not raw.strip():
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ConfigurationError(f"{name} должен быть конечным неотрицательным числом.") from None
        if not value.is_finite() or value < 0:
            raise ConfigurationError(f"{name} должен быть конечным неотрицательным числом.")
        return value

    trust = (values.get("LLM_USE_SYSTEM_TRUST") or "true").strip().lower()
    if trust not in {"true", "false"}:
        raise ConfigurationError("LLM_USE_SYSTEM_TRUST должен быть true или false.")

    return LLMOptions(
        max_tokens=integer("LLM_MAX_TOKENS", 8192),
        max_input_chars=integer("LLM_MAX_INPUT_CHARS", 100000),
        timeout_seconds=integer("LLM_TIMEOUT_SECONDS", 120),
        price_input_per_1m=price("PRICE_INPUT_PER_1M"),
        price_output_per_1m=price("PRICE_OUTPUT_PER_1M"),
        use_system_trust=trust == "true",
    )
