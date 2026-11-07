"""Real-model defense rehearsal in an isolated SQLite snapshot; no runtime mocks."""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.batch_reporting import make_report_directory, save_json_report, usage_from_calls
from backend.checklist import get_checklist, save_checklist
from backend.config import ConfigurationError, load_data_settings, read_environment
from backend.db import connect_db, init_db
from backend.main import create_app
from backend.scoring import calculate_scores


class DemoFailure(RuntimeError):
    pass


def _snapshot(source, destination):
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as origin:
        with closing(sqlite3.connect(destination)) as target:
            origin.backup(target)


def _copy_active_checklist(source, destination):
    """Network demos load only fresh records while retaining the working rubric."""
    if not Path(source).is_file():
        return None
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as origin:
        active = get_checklist(origin)
    with closing(connect_db(destination)) as target:
        copied = save_checklist(target, active, checklist_id=active["checklist_id"])
    return {"source_version_id": active["checklist_version_id"], "copied_version_id": copied["checklist_version_id"],
            "source_version": active["version"], "copy_preserves_config": True}


def _choose_conversation(conn, group_id, requested=None):
    if requested:
        row = conn.execute("SELECT conversation_id FROM conversations WHERE conversation_id=? AND dataset_group_id=?", (requested, group_id)).fetchone()
        if row is None:
            raise DemoFailure("Указанный диалог отсутствует в выбранной группе.")
        return row[0]
    # Prefer a previously observed N/A example without embedding source content.
    for row in conn.execute("SELECT e.conversation_id,e.final_result_json FROM evaluations e JOIN conversations c ON c.conversation_id=e.conversation_id WHERE c.dataset_group_id=? AND e.status='completed' AND c.channel='agent chat' ORDER BY e.created_at DESC", (group_id,)):
        result = json.loads(row[1] or "{}")
        if any(c.get("answer") == "na" for c in result.get("criteria", [])):
            return row[0]
    row = conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id LIMIT 1", (group_id,)).fetchone()
    if row is None:
        raise DemoFailure("В группе нет диалогов для демонстрации.")
    return row[0]


def _decision_target(result, requested_criterion=None, requested_answer=None):
    definitions = {c["criterion_id"]: c for c in result["checklist"]["criteria"]}
    candidates = [c for c in result["criteria"] if c.get("valid") and definitions[c["criterion_id"]].get("scored") and Decimal(str(definitions[c["criterion_id"]]["weight"])) > 0]
    if requested_criterion:
        candidates = [c for c in candidates if c["criterion_id"] == requested_criterion]
    if not candidates:
        raise DemoFailure("Нет валидного балльного критерия для проверки пересчёта.")
    criterion = candidates[0]
    definition = definitions[criterion["criterion_id"]]
    values = list(definition["scale"]) if definition["type"] == "scale" else ["yes", "no"]
    if definition.get("allow_na"):
        values.append("na")
    alternative = requested_answer or next(value for value in values if value != criterion["answer"])
    if alternative not in values or alternative == criterion["answer"]:
        raise DemoFailure("Ответ решения должен отличаться от исходного и быть допустимым.")
    return criterion, alternative


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default="DEV")
    parser.add_argument("--from-db", action="store_true", help="Явно взять локальный набор; импорт организаторов по сети не считается проверенным.")
    parser.add_argument("--conversation-id")
    parser.add_argument("--criterion-id")
    parser.add_argument("--decision-answer")
    parser.add_argument("--decision-reason", default="Демонстрационное решение проверяющего для проверки пересчёта. Это не экспертная разметка; изменение хранится только в изолированной копии БД.")
    parser.add_argument("--ai-suggest", action="store_true", help="Дополнительно проверить AI-подсказку реальным вызовом модели.")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 8:
        parser.error("--batch-size должен быть от 1 до 8")
    report = {"source": "local_snapshot" if args.from_db else "dataset_api", "network_load_verified": False,
              "steps": [], "demonstration_override": True, "is_ground_truth": False}
    folder, database = None, None
    started = time.monotonic()
    secret = read_environment().get("MODEL_API_KEY") or ""

    def emit(text):
        print(text.replace(secret, "[REDACTED]") if secret else text, flush=True)

    def step(name, message, ok=True):
        report["steps"].append({"step": name, "ok": ok, "message": message})
        emit(("ОК" if ok else "ОШИБКА") + " — " + name + ": " + message)

    def api(client, name, method, path, payload=None):
        response = client.request(method, path, json=payload) if payload is not None else client.request(method, path)
        value = response.json()
        save_json_report(folder / (name + ".json"), {"request": {"method": method, "path": path, "body": payload}, "status_code": response.status_code, "body": value})
        if response.status_code not in {200, 201, 202}:
            error = value.get("error", {})
            raise DemoFailure(f"{name}: HTTP {response.status_code}, {error.get('code', 'API_ERROR')}: {error.get('message', 'Запрос не выполнен.')}")
        return value

    try:
        settings = load_data_settings()
        if not args.from_db and not settings.api_url:
            step("загрузка", "DATASET_API_URL пуст; живой запрос не выполнен. Локальный сценарий запускается явно с --from-db.", False)
            return 1
        folder = make_report_directory("demo", args.report_dir)
        database = folder / "demo.sqlite3"
        if database.exists():
            raise DemoFailure("Выберите новую папку отчёта: существующая демонстрационная БД не перезаписывается.")
        if args.from_db:
            _snapshot(settings.database_path, database)
        else:
            init_db(database)
            report["checklist_copy"] = _copy_active_checklist(settings.database_path, database)
        with closing(connect_db(database)) as conn:
            initial_calls = {r[0] for r in conn.execute("SELECT call_id FROM llm_calls")}
        with TestClient(create_app(database)) as client:
            if args.from_db:
                step("загрузка", "Использована изолированная копия локального набора; внешний API не проверен.")
            else:
                loaded = api(client, "load", "POST", "/api/dataset/load", {"group_id": args.group_id})
                report["network_load_verified"] = True
                step("загрузка", f"Получено диалогов: {loaded['total']}.")
            with closing(connect_db(database)) as conn:
                cid = _choose_conversation(conn, args.group_id, args.conversation_id)
            result = api(client, "evaluation", "POST", "/api/evaluations", {"conversation_id": cid, "output_language": "ru"})
            if result["status"] != "completed":
                raise DemoFailure("Оценка требует проверки: " + json.dumps(result.get("validation_errors", []), ensure_ascii=False))
            if not any(c.get("answer") == "na" for c in result["criteria"]):
                raise DemoFailure("В выбранном диалоге модель не вернула N/A; выберите другой --conversation-id для этого пункта демонстрации.")
            if not any(c.get("evidence") for c in result["criteria"]):
                raise DemoFailure("Оценка не содержит доказательств для демонстрации.")
            step("оценка", f"{cid}: итог {result['total']}; N/A и доказательства присутствуют.")
            emit(json.dumps({"evaluation_id": result["evaluation_id"], "total": result["total"], "gross_violation": result["gross_violation"], "criteria": [{key: c.get(key) for key in ("criterion_id", "answer", "points", "effective_weight", "reasoning", "evidence")} for c in result["criteria"]]}, ensure_ascii=False, indent=2))
            original = copy.deepcopy(result["original"])
            criterion, final_answer = _decision_target(result, args.criterion_id, args.decision_answer)
            appeal = api(client, "appeal", "POST", "/api/appeals", {"evaluation_id": result["evaluation_id"], "criterion_id": criterion["criterion_id"], "comment": "Демонстрационная апелляция для проверки процесса пересмотра в изолированной копии. Комментарий не считается фактом о диалоге."})
            if args.ai_suggest:
                api(client, "suggestion", "POST", f"/api/appeals/{appeal['appeal_id']}/ai-suggest", {"output_language": "ru"})
                unchanged = api(client, "after_suggestion", "GET", f"/api/evaluations/{result['evaluation_id']}")
                if unchanged["original"] != original or unchanged["total"] != result["total"]:
                    raise DemoFailure("AI-подсказка изменила оценку.")
            decision = api(client, "decision", "POST", f"/api/appeals/{appeal['appeal_id']}/decision", {"decision": "change", "final_answer": final_answer, "reason": args.decision_reason})
            final = api(client, "after_decision", "GET", f"/api/evaluations/{result['evaluation_id']}")
            if final["original"] != original:
                raise DemoFailure("Исходная оценка изменилась после решения.")
            expected = calculate_scores(final["checklist"], {c["criterion_id"]: c["answer"] for c in final["criteria"]})["total"]
            if (Decimal(final["total"]) if final["total"] is not None else None) != expected:
                raise DemoFailure("Финальный итог не совпал с независимым пересчётом.")
            report["appeal"] = decision
            step("апелляция и решение", f"Итог {decision['before_total']} → {decision['after_total']}; оригинал сохранён. Решение демонстрационное, не эталон.")
            old = api(client, "old_checklist", "GET", "/api/checklists/active")
            new = {k: copy.deepcopy(old[k]) for k in ("checklist_id", "name", "sections", "criteria")}
            replaced = next(c for c in new["criteria"] if c["criterion_id"] == criterion["criterion_id"])
            new_id = "demo_plain_language"
            if any(c["criterion_id"] == new_id for c in new["criteria"]):
                raise DemoFailure("Демонстрационный criterion_id уже занят.")
            replaced.update(criterion_id=new_id, name="Понятный язык оператора", description="Оператор объясняет действия понятными фразами без необъяснённого профессионального жаргона.", type="yes_no", allow_na=False, na_condition=None, scale={}, yes_text="Фразы оператора понятны; профессиональные термины объяснены либо отсутствуют.", no_text="Есть необъяснённый профессиональный жаргон, затрудняющий понимание.")
            version = api(client, "new_checklist", "POST", "/api/checklists", new)
            with closing(connect_db(database)) as conn:
                other = [r[0] for r in conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? AND conversation_id<>? ORDER BY conversation_id LIMIT ?", (args.group_id, cid, args.batch_size - 1))]
            selected = [cid] + other
            job = api(client, "batch_submit", "POST", "/api/evaluations/batch", {"conversation_ids": selected, "checklist_version_id": version["checklist_version_id"], "criterion_ids": [new_id], "force": True})
            while True:
                response = client.get("/api/jobs/" + job["job_id"])
                response.raise_for_status()
                job = response.json()
                if job["status"] in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(.2)
            save_json_report(folder / "batch_job.json", job)
            if job["status"] != "completed" or job["errors"]:
                raise DemoFailure("Переоценка завершена с ошибками: " + json.dumps(job["errors"], ensure_ascii=False))
            for index, item in enumerate(job["items"]):
                changed = api(client, f"reevaluation_{index}", "GET", "/api/evaluations/" + item["evaluation_id"])
                if changed["status"] != "completed" or new_id not in {c["criterion_id"] for c in changed["criteria"]}:
                    raise DemoFailure("Новый критерий не применён.")
                expected = calculate_scores(changed["checklist"], {c["criterion_id"]: c["answer"] for c in changed["criteria"]})["total"]
                if (Decimal(changed["total"]) if changed["total"] is not None else None) != expected:
                    raise DemoFailure("Переоценка не совпала с независимым расчётом.")
            step("замена критерия", f"Новая версия {version['version']}, обработано {len(selected)} диалогов; итоги пересчитаны.")
            api(client, "cost", "GET", "/api/stats/cost")
            with closing(connect_db(database)) as conn:
                rows = [r for r in conn.execute("SELECT * FROM llm_calls") if r["call_id"] not in initial_calls]
            report["usage"] = usage_from_calls(rows)
            step("стоимость", json.dumps(report["usage"], ensure_ascii=False))
        report["ok"] = True
        return 0
    except (ConfigurationError, DemoFailure, OSError, ValueError) as exc:
        step("сценарий", str(exc) if not isinstance(exc, OSError) else "Не удалось открыть локальные файлы сценария.", False)
        report["ok"] = False
        return 1
    except Exception:
        step("сценарий", "Неожиданная ошибка; фиктивный результат не создан.", False)
        report["ok"] = False
        return 1
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        completed_steps = {item["step"] for item in report["steps"]}
        for name in ("загрузка", "оценка", "апелляция и решение", "замена критерия", "стоимость"):
            if name not in completed_steps:
                step(name, "Не выполнено из-за предыдущей ошибки; результат не имитировался.", False)
        if folder:
            destination = folder / "demo_report.json"
            if not destination.exists():
                save_json_report(destination, report)
                emit("REPORT: " + str(destination))
            else:
                emit("REPORT_EXISTS: прежний отчёт не перезаписан: " + str(destination))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
