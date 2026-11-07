"""Measure agreement with a human-filled labeling CSV; blanks remain unscored."""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.labeling import LabelingError, labeling_agreement, report_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON report inside data/reports/; existing files are not replaced.")
    args = parser.parse_args(argv)
    try:
        output = report_path(args.output) if args.output else None
        result = labeling_agreement(args.csv_path)
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
    except (LabelingError, UnicodeError, csv.Error) as exc:
        print(f"LABELING_AGREEMENT_ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("LABELING_AGREEMENT_ERROR: не удалось прочитать CSV или создать новый отчёт.", file=sys.stderr)
        return 1
    print(encoded)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
