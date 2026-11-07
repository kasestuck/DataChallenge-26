"""Run with: python scripts/list_models.py [--env-file /path/to/.env]."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, load_settings
from backend.meta_client import ModelRequestError, list_meta_models


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Получить ID моделей через API /models без повторов.")
    parser.add_argument("--env-file", type=Path, help="Необязательный путь к существующему .env.")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file, require_model=False)
        model_ids = list_meta_models(settings=settings)
    except ConfigurationError as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 2
    except ModelRequestError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "UNEXPECTED_ERROR: не удалось прочитать список моделей. "
            "Проверьте зависимости и совместимость API /models.",
            file=sys.stderr,
        )
        return 1

    if not model_ids:
        print("API вернул пустой список моделей; доступных ID в ответе нет.")
    for model_id in model_ids:
        print(model_id.replace(settings.api_key, "[REDACTED]"))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
