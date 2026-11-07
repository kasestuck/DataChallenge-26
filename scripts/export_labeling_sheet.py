"""Export a diverse saved sample for human labeling, without model calls."""
import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, load_data_settings
from backend.db import connect_db, init_db
from backend.labeling import LabelingError, export_labeling_sheet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default="DEV")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--checklist-version-id")
    parser.add_argument("--output-dir", default="data/reports/labeling")
    args = parser.parse_args(argv)
    try:
        path = load_data_settings().database_path
        init_db(path)
        with closing(connect_db(path)) as conn:
            result = export_labeling_sheet(conn, count=args.count, group_id=args.group_id,
                                           checklist_version_id=args.checklist_version_id, output_dir=args.output_dir)
    except (LabelingError, ConfigurationError, LookupError) as exc:
        print(f"LABELING_EXPORT_ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("LABELING_EXPORT_ERROR: не удалось прочитать БД или записать файлы.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
