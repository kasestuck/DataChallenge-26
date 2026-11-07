"""Run with: python scripts/smoke_test.py [--env-file /path/to/.env]."""

import argparse
import json
import sys
from pathlib import Path

# Support invocation by filename from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, load_settings
from backend.meta_client import ModelRequestError, ask_meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Один реальный запрос к настроенной модели без повторов.")
    parser.add_argument("--env-file", type=Path, help="Необязательный путь к существующему .env.")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
        result = ask_meta(
            "You are a connectivity test assistant. Reply briefly.",
            "Reply with exactly: CQA smoke test OK",
            settings=settings,
        )
    except ConfigurationError as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 2
    except ModelRequestError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # SDK/parsing errors may contain request headers or provider-controlled bodies.
        print(
            "UNEXPECTED_ERROR: непредвиденная ошибка SDK или формата ответа. "
            "Smoke test не пройден; проверьте зависимости и совместимость API.",
            file=sys.stderr,
        )
        return 1

    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output.replace(settings.api_key, "[REDACTED]"))
    if result["usage"] is None:
        print("Usage: API не предоставил данные о токенах.")
    print("Smoke test пройден: получен непустой ответ модели на реальный запрос.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
