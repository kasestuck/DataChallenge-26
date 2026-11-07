"""Load a group from the configured API and evaluate it idempotently."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.batch_reporting import compact_report, make_report_directory, run_batch_report, save_json_report
from backend.checklist import get_checklist
from backend.consensus import run_consensus_report
from backend.config import ConfigurationError, load_data_settings
from backend.dataset import DatasetError, fetch_dataset, import_dataset
from backend.db import connect_db, init_db, write_transaction
from backend.submission import SubmissionError, validate_group_records, validate_official_checklist
from scripts.export_results import export_stored_results, write_submission


def _prepare_rehearsal(source_path, folder, group_id):
    """Copy ten real DEV chats into an isolated, explicitly named rehearsal group."""
    path, manifest_path = folder / "rehearsal.sqlite3", folder / "rehearsal_source.json"
    if path.exists():
        if not manifest_path.exists():
            raise ValueError("Копия репетиции существует без описания. Используйте новую папку отчёта.")
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        if metadata.get("source_database") != str(Path(source_path).resolve()) or metadata.get("source_group_id") != group_id:
            raise ValueError("Папка репетиции относится к другому исходному набору.")
        return path, metadata["group_id"], metadata["conversation_ids"]
    with closing(sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)) as source:
        pools = {language: [row[0] for row in source.execute(
            "SELECT conversation_id FROM conversations WHERE dataset_group_id=? AND channel='agent chat' AND language=? ORDER BY conversation_id",
            (group_id, language))] for language in ("RU", "KZ", "EN")}
        if any(not values for values in pools.values()):
            raise ValueError("Для репетиции нужны чаты DEV на RU, KZ и EN.")
        selected = []
        while len(selected) < 10 and any(pools.values()):
            for language in ("RU", "KZ", "EN"):
                if pools[language] and len(selected) < 10:
                    selected.append(pools[language].pop(0))
        if len(selected) != 10:
            raise ValueError("В DEV недостаточно десяти чатов для репетиции.")
        with closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
    init_db(path)
    rehearsal_group = "DEV_REHEARSAL"
    with closing(connect_db(path)) as conn, write_transaction(conn):
        if conn.execute("SELECT 1 FROM conversations WHERE dataset_group_id=?", (rehearsal_group,)).fetchone():
            raise ValueError("В исходной копии уже есть группа DEV_REHEARSAL; используйте чистый исходный набор.")
        conn.executemany("UPDATE conversations SET dataset_group_id=? WHERE conversation_id=?", [(rehearsal_group, cid) for cid in selected])
    metadata = {"source_database": str(Path(source_path).resolve()), "source_group_id": group_id,
                "group_id": rehearsal_group, "conversation_ids": selected,
                "is_real_control_set": False, "source": "ten_chats_from_dev"}
    save_json_report(manifest_path, metadata)
    return path, rehearsal_group, selected


def _save_rehearsal_submission(folder, payload):
    """A DEV rehearsal can never create results/prod/results.json."""
    path = folder / "results.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) == payload:
            return {"path": str(path), "changed": False}
        path = folder / ("results_" + uuid4().hex[:10] + ".json")
    save_json_report(path, payload)
    return {"path": str(path), "changed": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group_id")
    parser.add_argument("--from-db", action="store_true", help="Явно использовать уже загруженный набор; сетевой импорт не проверяется.")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--export-results", action="store_true", help="После batch сохранить официальный results/<split>/results.json.")
    parser.add_argument("--split", choices=["dev", "prod"], default="prod")
    parser.add_argument("--team-name")
    parser.add_argument("--replace-results", action="store_true", help="Заменить существующий results.json с резервной копией.")
    parser.add_argument("--runs", type=int, choices=[1, 3], default=1, help="1: одна оценка; 3: независимые прогоны и большинство по каждому критерию.")
    parser.add_argument("--rehearsal", action="store_true", help="Только DEV: отдельная SQLite и выгрузка в data/reports, никогда не в results/prod.")
    parser.add_argument("--limit", type=int, help="Для репетиции укажите 10; произвольное усечение настоящего набора запрещено.")
    args = parser.parse_args(argv)
    if (args.export_results or args.runs == 3) and (not args.team_name or not args.team_name.strip()):
        parser.error("Для --export-results или --runs 3 требуется --team-name с названием команды.")
    if args.rehearsal and (not args.from_db or args.group_id != "DEV" or args.limit != 10 or args.split != "prod"):
        parser.error("Репетиция требует DEV --from-db --rehearsal --limit 10 --split prod; это не настоящий контрольный набор.")
    if args.limit is not None and not args.rehearsal:
        parser.error("--limit разрешён только вместе с --rehearsal; набор сдачи не усекается.")
    try:
        settings = load_data_settings()
        if not args.group_id.strip():
            raise DatasetError("INVALID_GROUP_ID", "group_id не должен быть пустым.")
        if not args.from_db and not settings.api_url:
            raise DatasetError("DATASET_NOT_CONFIGURED", "DATASET_API_URL пуст: живой запрос не выполнен. Для явной проверки уже загруженного набора используйте --from-db.")
        folder = make_report_directory("control", args.report_dir) if args.rehearsal else None
        database_path, group_id = settings.database_path, args.group_id
        if args.rehearsal:
            database_path, group_id, _ = _prepare_rehearsal(database_path, folder, args.group_id)
        init_db(database_path)
        with closing(connect_db(database_path)) as conn:
            if args.from_db:
                imported = None
                ids = [row[0] for row in conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id", (group_id,))]
            else:
                payload = fetch_dataset(settings, args.group_id)
                imported = import_dataset(conn, payload, expected_group_id=args.group_id)
                ids = [record["conversation_id"] for record in payload["data"]["records"]]
            if not ids:
                raise DatasetError("DATASET_EMPTY", "В указанной группе нет сохранённых диалогов.")
            checklist = get_checklist(conn)
            if args.export_results or args.runs == 3:
                validate_official_checklist(checklist)
                records = [dict(conn.execute("SELECT conversation_id,channel FROM conversations WHERE conversation_id=?", (cid,)).fetchone()) for cid in ids]
                validate_group_records(records, args.split)
        folder = folder or make_report_directory("control", args.report_dir)
        def progress(event):
            print(json.dumps({"progress": event}, ensure_ascii=False), flush=True)
        request = {"conversation_ids": ids, "checklist_version_id": checklist["checklist_version_id"], "force": False}
        if args.runs == 3:
            report = asyncio.run(run_consensus_report(database_path, request, team_name=args.team_name, progress=progress))
        else:
            report = asyncio.run(run_batch_report(database_path, request, progress=progress))
        report.update(group_id=group_id, source="dev_rehearsal_snapshot" if args.rehearsal else "local_database" if args.from_db else "dataset_api",
                      network_load_verified=not args.from_db, import_result=imported, runs=args.runs)
        if args.rehearsal:
            report.update(database_path=str(database_path), source_group_id=args.group_id, is_real_control_set=False)
        report_path = folder / "control_report.json"
        if report_path.exists():
            report_path = folder / ("control_report_" + uuid4().hex[:10] + ".json")
        save_json_report(report_path, report)
        print(json.dumps({key: value for key, value in compact_report(report).items() if key != "submission"}, ensure_ascii=False, indent=2), flush=True)
        print("REPORT: " + str(report_path))
        if args.export_results:
            if args.runs == 3:
                payload, metadata = report["submission"], report["submission_metadata"]
                exported = (_save_rehearsal_submission(folder, payload) if args.rehearsal else
                            write_submission(payload, metadata, split=args.split, replace=args.replace_results))
                exported["status_counts"] = {"ok": sum(r["status"] == "ok" for r in payload["results"]),
                                             "error": sum(r["status"] == "error" for r in payload["results"])}
            elif args.rehearsal:
                from backend.submission import build_submission
                with closing(connect_db(database_path)) as conn:
                    payload, _ = build_submission(conn, group_id=group_id, split=args.split,
                        team_name=args.team_name, checklist_version_id=checklist["checklist_version_id"], conversation_ids=ids)
                exported = _save_rehearsal_submission(folder, payload)
                exported["status_counts"] = {"ok": sum(r["status"] == "ok" for r in payload["results"]),
                                             "error": sum(r["status"] == "error" for r in payload["results"])}
            else:
                exported = export_stored_results(database_path, group_id=group_id,
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
