"""Import a downloaded organizer envelope or fetch it through the backend."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, load_data_settings
from backend.dataset import DatasetError, import_dataset, load_dataset
from backend.db import connect_db, init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт DEV/контрольного набора без обращения к модели.")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--group-id", help="Заменяет DATASET_GROUP_ID для одного запуска.")
    parser.add_argument("--file", type=Path, help="JSON-ответ организаторов; без запроса к API.")
    args = parser.parse_args(argv)
    try:
        settings = load_data_settings(args.env_file)
        payload = None
        if args.file is not None:
            if args.file.stat().st_size > settings.max_dataset_bytes:
                raise DatasetError("DATASET_TOO_LARGE", "Файл превышает MAX_DATASET_BYTES; импорт отменён.")
            try:
                payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError):
                raise DatasetError("DATASET_INVALID_JSON", "Файл не содержит корректного JSON.") from None
        init_db(settings.database_path)
        conn = connect_db(settings.database_path)
        try:
            if args.file is not None:
                result = import_dataset(conn, payload, expected_group_id=args.group_id)
            else:
                result = load_dataset(conn, settings, args.group_id)
        finally:
            conn.close()
    except (DatasetError, ConfigurationError) as exc:
        print(f"{getattr(exc, 'code', 'CONFIGURATION_ERROR')}: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("FILE_ERROR: не удалось прочитать файл или открыть каталог данных.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
