"""Load a group from the configured API and evaluate it idempotently."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.batch_reporting import compact_report, make_report_directory, run_batch_report, save_json_report
from backend.checklist import get_checklist
from backend.config import ConfigurationError, load_data_settings
from backend.dataset import DatasetError, fetch_dataset, import_dataset
from backend.db import connect_db, init_db
from backend.submission import SubmissionError, validate_group_records, validate_official_checklist
from scripts.export_results import export_stored_results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group_id")
    parser.add_argument("--from-db", action="store_true", help="Явно использовать уже загруженный набор; сетевой импорт не проверяется.")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--export-results", action="store_true", help="После batch сохранить официальный results/<split>/results.json.")
    parser.add_argument("--split", choices=["dev", "prod"], default="prod")
    parser.add_argument("--team-name")
    parser.add_argument("--replace-results", action="store_true", help="Заменить существующий results.json с резервной копией.")
    args = parser.parse_args(argv)
    if args.export_results and (not args.team_name or not args.team_name.strip()):
        parser.error("Для --export-results требуется --team-name с названием команды.")
    try:
        settings = load_data_settings()
        if not args.group_id.strip():
            raise DatasetError("INVALID_GROUP_ID", "group_id не должен быть пустым.")
        if not args.from_db and not settings.api_url:
            raise DatasetError("DATASET_NOT_CONFIGURED", "DATASET_API_URL пуст: живой запрос не выполнен. Для явной проверки уже загруженного набора используйте --from-db.")
        init_db(settings.database_path)
        with closing(connect_db(settings.database_path)) as conn:
            if args.from_db:
                imported = None
                ids = [row[0] for row in conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id", (args.group_id,))]
            else:
                payload = fetch_dataset(settings, args.group_id)
                imported = import_dataset(conn, payload, expected_group_id=args.group_id)
                ids = [record["conversation_id"] for record in payload["data"]["records"]]
            if not ids:
                raise DatasetError("DATASET_EMPTY", "В указанной группе нет сохранённых диалогов.")
            checklist = get_checklist(conn)
            if args.export_results:
                validate_official_checklist(checklist)
                records = [dict(conn.execute("SELECT conversation_id,channel FROM conversations WHERE conversation_id=?", (cid,)).fetchone()) for cid in ids]
                validate_group_records(records, args.split)
        folder = make_report_directory("control", args.report_dir)
        def progress(event):
            print(json.dumps({"progress": event}, ensure_ascii=False), flush=True)
        report = asyncio.run(run_batch_report(settings.database_path, {"conversation_ids": ids, "checklist_version_id": checklist["checklist_version_id"], "force": False}, progress=progress))
        report.update(group_id=args.group_id, source="local_database" if args.from_db else "dataset_api", network_load_verified=not args.from_db, import_result=imported)
        report_path = folder / "control_report.json"
        if report_path.exists():
            report_path = folder / ("control_report_" + uuid4().hex[:10] + ".json")
        save_json_report(report_path, report)
        print(json.dumps(compact_report(report), ensure_ascii=False, indent=2), flush=True)
        print("REPORT: " + str(report_path))
        if args.export_results:
            exported = export_stored_results(settings.database_path, group_id=args.group_id,
                split=args.split, team_name=args.team_name,
                checklist_version_id=checklist["checklist_version_id"],
                conversation_ids=ids, replace=args.replace_results)
            print(json.dumps({"submission": exported}, ensure_ascii=False, indent=2), flush=True)
            if exported["status_counts"]["error"]:
                return 2
        return 0 if report["status"] == "completed" and not report["counts"]["failed"] and not report["counts"]["needs_review"] else 2
    except (ConfigurationError, DatasetError, SubmissionError, ValueError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "CONFIGURATION_ERROR"), "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception:
        print('{"error":{"code":"CONTROL_PROCESS_FAILED","message":"Обработка не завершена; фиктивные оценки не созданы."}}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
