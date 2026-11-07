"""Real model quality runs. Captures remain under ignored data/reports/."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.batch_reporting import compact_report, make_report_directory, run_batch_report, save_json_report
from backend.checklist import activate_checklist, get_checklist, save_checklist
from backend.config import load_data_settings, load_llm_options
from backend.dataset import get_conversation, import_dataset
from backend.db import connect_db, init_db
from backend.evaluator import PROMPT_PATH, build_prompts, get_evaluation
from backend.quality import answer_distributions, jargon_checklist, latest_completed, repeatability, select_diverse, verify_partial_result


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def run_phase(phase, folder, database_path, group_id, state):
    version_id = state["base_version_id"]
    current_hash = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()
    if current_hash != state["prompt_hash"]:
        raise ValueError("Файл промпта изменился после начала серии; используйте новую папку отчёта.")
    if phase == "dev":
        with closing(connect_db(database_path)) as conn:
            ids = [row[0] for row in conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id", (group_id,))]
        if not ids:
            raise ValueError("В БД нет диалогов указанного набора.")
        report = await run_batch_report(database_path, {"conversation_ids": ids, "checklist_version_id": version_id, "force": False}, progress=emit)
        report["distributions"] = answer_distributions(report["evaluations"])
        report["prompt_hash_cohorts"] = {key: sum((e.get("prompt_hash") or "legacy_unknown") == key for e in report["evaluations"]) for key in report["prompt_hashes"]}
        report["cohort_note"] = "Готовые оценки пропущены по запросу; legacy_unknown не приписывается текущему промпту."
        return report
    with closing(connect_db(database_path)) as conn:
        eligible = latest_completed(conn, group_id, version_id)
    if phase == "repeat":
        ids = select_diverse(eligible, count=5, identification=True)
        runs = []
        if any((folder / f"repeat-run-{number}.json").exists() for number in range(1, 4)):
            raise ValueError("Уже существует захват повтора; для новой независимой серии используйте новый каталог.")
        for number in range(1, 4):
            destination = folder / f"repeat-run-{number}.json"
            if destination.exists():
                raise ValueError("Уже существует захват повтора; повторно оплачивать его без нового каталога нельзя.")
            report = await run_batch_report(database_path, {"conversation_ids": ids, "checklist_version_id": version_id, "force": True}, progress=emit)
            save_json_report(destination, report)
            runs.append(report)
            if any(e.get("prompt_hash") != current_hash for e in report["evaluations"]):
                raise ValueError("Повторы использовали разные версии промпта.")
        return {"conversation_ids": ids, "checklist_version_id": version_id, "prompt_hash": current_hash,
                "runs": [compact_report(report) for report in runs],
                "metrics": repeatability([r["evaluations"] for r in runs], ids)}
    if phase == "criterion":
        ids = select_diverse(eligible, count=8)
        reports = []
        if any((folder / f"criterion-{label}.json").exists() for label in ("yes_no", "scale")):
            raise ValueError("Уже существует захват замены критерия; используйте новый каталог.")
        with closing(connect_db(database_path)) as conn:
            original_active = get_checklist(conn)["checklist_version_id"]
            original = get_checklist(conn, version_id)
        try:
            for scale, label in ((False, "yes_no"), (True, "scale")):
                with closing(connect_db(database_path)) as conn:
                    current = get_checklist(conn, reports[-1]["version"]["checklist_version_id"]) if reports else original
                    version = save_checklist(conn, jargon_checklist(current, scale=scale), checklist_id=current["checklist_id"])
                report = await run_batch_report(database_path, {"conversation_ids": ids, "checklist_version_id": version["checklist_version_id"], "criterion_ids": ["09"], "force": True}, progress=emit)
                verifications = []
                with closing(connect_db(database_path)) as conn:
                    for evaluation in report["evaluations"]:
                        baseline_id = evaluation.get("reevaluation", {}).get("baseline_evaluation_id")
                        baseline = get_evaluation(conn, baseline_id) if baseline_id else {"criteria": []}
                        verifications.append({"conversation_id": evaluation["conversation_id"], **verify_partial_result(evaluation, baseline)})
                capture = {"type": label, "version": version, "batch": report, "verification": verifications}
                save_json_report(folder / f"criterion-{label}.json", capture)
                reports.append(capture)
        finally:
            with closing(connect_db(database_path)) as conn:
                activate_checklist(conn, original_active)
        return {"conversation_ids": ids, "restored_active_version_id": original_active,
                "variants": [{"type": c["type"], "version_id": c["version"]["checklist_version_id"],
                              "batch": compact_report(c["batch"]), "verification": c["verification"]} for c in reports]}
    if phase == "edges":
        scratch = folder / "edges.sqlite3"
        if scratch.exists():
            raise ValueError("База крайних случаев уже существует; используйте новую папку отчёта.")
        with closing(connect_db(database_path)) as source, closing(sqlite3.connect(scratch)) as destination:
            source.backup(destination)
        cases = [
            {"conversation_id": "synthetic-no-operator", "channel": "agent chat", "language": "RU", "transcript": [
                {"timestamp": "00:00", "speaker": "client", "text": "Как открыть справку приложения?"},
                {"timestamp": "00:01", "speaker": "bot", "text": "Обращение ожидает подключения сотрудника."}]},
            {"conversation_id": "synthetic-short", "channel": "agent chat", "language": "RU", "transcript": [
                {"timestamp": "00:00", "speaker": "client", "text": "Здравствуйте."}]},
            {"conversation_id": "synthetic-no-question", "channel": "agent chat", "language": "RU", "transcript": [
                {"timestamp": "00:00", "speaker": "client", "text": "Просто проверяю, что окно чата открывается."},
                {"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте, я Алексей. Сообщение получено. Если понадобится помощь, напишите. До свидания."}]},
        ]
        with closing(connect_db(scratch)) as conn:
            longest = conn.execute("SELECT c.conversation_id,SUM(length(m.text)) AS chars FROM conversations c JOIN messages m ON m.conversation_id=c.conversation_id WHERE c.dataset_group_id=? GROUP BY c.conversation_id ORDER BY chars DESC,c.conversation_id LIMIT 1", (group_id,)).fetchone()
            if longest is None:
                raise ValueError("В указанном наборе нет длинного диалога для проверки.")
            import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "SYNTHETIC_EDGES", "total_records": len(cases), "records": cases}})
            ids = [case["conversation_id"] for case in cases] + [longest["conversation_id"]]
            metadata = []
            for cid in ids:
                conversation = get_conversation(conn, cid)
                system, user = build_prompts(conversation, get_checklist(conn, version_id))
                count = len(system) + len(user)
                metadata.append({"conversation_id": cid, "messages": len(conversation["transcript"]),
                                 "operator_messages": sum(m["speaker"] == "operator" for m in conversation["transcript"]),
                                 "transcript_chars": sum(len(m["text"]) for m in conversation["transcript"]),
                                 "input_chars": count, "input_limit": load_llm_options().max_input_chars,
                                 "warnings": (["NO_OPERATOR_MESSAGES"] if not any(m["speaker"] == "operator" for m in conversation["transcript"]) else [])
                                     + (["SYNTHETIC_NO_SPECIFIC_QUESTION"] if cid == "synthetic-no-question" else [])
                                     + (["MODEL_INPUT_TOO_LARGE"] if count > load_llm_options().max_input_chars else []),
                                 "is_longest_dev": cid == longest["conversation_id"]})
        report = await run_batch_report(scratch, {"conversation_ids": ids, "checklist_version_id": version_id, "force": True}, progress=emit)
        return {"scratch_database": str(scratch), "cases": metadata, "batch": report,
                "note": "N/A и ответы модели не исправляются вручную. Предупреждения описывают вход; полный сырой ответ и ошибки сохранены."}
    raise ValueError("Неизвестный этап проверки.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["dev", "repeat", "criterion", "edges"])
    parser.add_argument("--report-dir")
    parser.add_argument("--group-id", default="DEV")
    args = parser.parse_args(argv)
    try:
        folder = make_report_directory("stage5-quality", args.report_dir)
        target = folder / (args.phase + ".json")
        if target.exists():
            raise ValueError("Отчёт этапа уже существует; исходные захваты не перезаписываются.")
        database_path = load_data_settings().database_path
        init_db(database_path)
        state_path = folder / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            with closing(connect_db(database_path)) as conn:
                state = {"base_version_id": get_checklist(conn)["checklist_version_id"],
                         "prompt_hash": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(), "group_id": args.group_id, "phases": {}}
        if state["group_id"] != args.group_id:
            raise ValueError("Каталог отчёта принадлежит другому group_id.")
        # Persist the frozen version before any billable request, including failures.
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        report = asyncio.run(run_phase(args.phase, folder, database_path, args.group_id, state))
        save_json_report(target, report)
        state["phases"][args.phase] = str(target)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        visible = compact_report(report)
        if args.phase == "edges":
            visible["batch"] = compact_report(report["batch"])
        emit({"phase": args.phase, "report": str(target), "result": visible})
        return 0
    except Exception as exc:
        # Safe domain errors are actionable; do not disclose SDK/SQL/provider bodies.
        if isinstance(exc, (ValueError, FileExistsError)):
            emit({"error": {"code": getattr(exc, "code", "QUALITY_CHECK_ERROR"), "message": str(exc)}})
        else:
            emit({"error": {"code": "QUALITY_CHECK_ERROR", "message": "Проверка не завершена. Сохранённые результаты не подменены; проверьте конфигурацию и локальные отчёты."}})
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
