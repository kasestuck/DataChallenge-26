"""Evaluate one stored conversation, or export an existing evaluation without API calls."""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, load_data_settings, read_environment
from backend.db import connect_db, init_db
from backend.evaluator import evaluate_conversation, get_evaluation
from backend.meta_client import ModelRequestError


def render_report(report: dict, *, audit: bool = False) -> str:
    lines = [
        f"Диалог: {report['conversation_id']}",
        f"Оценка: {report['evaluation_id']}",
        f"Статус: {report['status']}",
        f"Версия чек-листа: {report['checklist_version_id']}",
    ]
    checklist = report.get("checklist") or {}
    names = {c["criterion_id"]: c["name"] for c in checklist.get("criteria", [])}
    for row in report.get("criteria", []):
        cid = row["criterion_id"]
        lines += ["", f"{cid}. {names.get(cid, '')}",
                  f"  Ответ: {row.get('answer')}; баллы: {row.get('points')}; "
                  f"эффективный вес: {row.get('effective_weight')}",
                  f"  Обоснование: {row.get('reasoning') or 'Не получено валидного обоснования.'}"]
        for evidence in row.get("evidence", []):
            lines.append(f"  [{evidence.get('message_id')}] {evidence.get('speaker')}: {evidence.get('quote')}")
        if row.get("checked_range"):
            lines.append(f"  Проверенный участок: {json.dumps(row['checked_range'], ensure_ascii=False)}")
        if row.get("validation_errors"):
            lines.append("  Ошибки: " + json.dumps(row["validation_errors"], ensure_ascii=False))
    lines += ["", f"Итог: {report.get('total')}", f"Грубое нарушение: {report.get('gross_violation')}"]
    usage = report.get("usage") or {}
    cost = usage.get("cost")
    cost_label = str(cost) if cost is not None else (
        "цена не задана" if usage.get("price_status") != "configured" else "неизвестна: неполный учёт usage"
    )
    lines += [
        f"Вызовов API: {usage.get('calls')}",
        f"Токены: вход {usage.get('prompt_tokens')}, выход {usage.get('completion_tokens')}, всего {usage.get('total_tokens')}",
        f"Стоимость: {cost_label}",
    ]
    if not usage.get("usage_complete", False):
        lines.append("Внимание: показаны только известные токены; API предоставил usage не для всех вызовов.")
    if report.get("error"):
        lines.append("Ошибка: " + json.dumps(report["error"], ensure_ascii=False))
    attempts = report.get("validation_attempts", [])
    lines.append("Проверки и повторы: " + json.dumps(attempts, ensure_ascii=False, indent=2))
    if audit:
        snapshot = report.get("conversation_snapshot") or {}
        lines += ["", "ТРАНСКРИПТ (исходные реплики, без исправлений)"]
        for message in snapshot.get("transcript", []):
            lines.append(f"[{message['message_id']}] {message['timestamp']} {message['speaker']}: {message['text']}")
        lines += ["", "СЫРЫЕ ОТВЕТЫ МОДЕЛИ (включая невалидные попытки)"]
        for response in report.get("raw_responses", []):
            lines.append(f"Попытка валидации: {response.get('validation_attempt')}; finish_reason={response.get('finish_reason')}")
            lines.append(response.get("content") or "[Пустой ответ]")
        calls = [{k: v for k, v in call.items() if k != "raw_response"} for call in report.get("llm_calls", [])]
        lines += ["", "ВСЕ ВЫЗОВЫ API (метаданные)", json.dumps(calls, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Оценка одного диалога реальной моделью.")
    parser.add_argument("conversation_id", help="ID диалога; с --saved: ID уже сохранённой оценки.")
    parser.add_argument("--lang", choices=("ru", "kk", "en"), default="ru")
    parser.add_argument("--checklist-version-id")
    parser.add_argument("--saved", action="store_true", help="Прочитать сохранённую оценку без обращения к API.")
    parser.add_argument("--audit", action="store_true", help="Показать транскрипт и сырые ответы в терминале.")
    parser.add_argument("--report-dir", type=Path, help="Сохранить полный аудит в JSON и TXT (используйте data/).")
    args = parser.parse_args(argv)
    key = ""

    def safe(text: str) -> str:
        return text.replace(key, "[REDACTED]") if key else text

    try:
        key = (read_environment().get("MODEL_API_KEY") or "").strip()
        settings = load_data_settings()
        init_db(settings.database_path)
        conn = connect_db(settings.database_path)
        try:
            if args.saved:
                report = get_evaluation(conn, args.conversation_id)
            else:
                result = evaluate_conversation(
                    conn, args.conversation_id,
                    checklist_version_id=args.checklist_version_id,
                    output_language=args.lang,
                )
                report = get_evaluation(conn, result["evaluation_id"])
        finally:
            conn.close()
        # Only redacts console/export output. The database retains exact model responses.
        print(safe(render_report(report, audit=args.audit)))
        if args.report_dir:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", report["conversation_id"]) + "_" + report["evaluation_id"]
            # Export only required transcript fields, not signed recording URLs.
            export = dict(report)
            snapshot = dict(export.get("conversation_snapshot") or {})
            snapshot.pop("audio_url", None)
            export["conversation_snapshot"] = snapshot
            for suffix, content in (
                (".json", json.dumps(export, ensure_ascii=False, indent=2)),
                (".txt", render_report(report, audit=True)),
            ):
                path = args.report_dir / (stem + suffix)
                with path.open("x", encoding="utf-8") as output:
                    output.write(safe(content) + "\n")
                print(f"Отчёт: {path.resolve()}")
        return 0 if report["status"] == "completed" else (2 if report["status"] == "needs_review" else 1)
    except ConfigurationError as exc:
        print(safe(f"CONFIGURATION_ERROR: {exc}"), file=sys.stderr)
        return 2
    except (ModelRequestError, LookupError, ValueError) as exc:
        print(safe(f"{getattr(exc, 'code', 'EVALUATION_ERROR')}: {exc}"), file=sys.stderr)
        return 1
    except Exception:
        print("INTERNAL_ERROR: оценка или экспорт не завершены. Проверьте состояние БД и доступ к каталогу отчётов.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
