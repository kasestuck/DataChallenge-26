"""Initialize stage 1 without connecting to a model."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import load_data_settings
from backend.db import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Создать SQLite-схему и seed чек-листа.")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    settings = load_data_settings(args.env_file)
    init_db(settings.database_path)
    print("SQLite initialized; checklist seed is idempotent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
