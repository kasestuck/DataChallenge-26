"""Audit a submission file without modifying it, the dataset, or saved evaluations."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.checklist import get_checklist
from backend.config import load_data_settings, read_environment
from backend.dataset import fetch_dataset, get_conversation
from backend.submission_validation import inspect_submission, read_submission_file


class ReferenceUnavailable(ValueError):
    pass


def _read_database(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ReferenceUnavailable("Локальная БД отсутствует; состав набора и журнал модели не проверены.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _database_records(conn, group_id):
    rows = conn.execute("SELECT conversation_id,channel,raw_json FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id", (group_id,)).fetchall()
    if not rows:
        raise ReferenceUnavailable("В локальной БД нет указанного набора.")
    records = []
    for row in rows:
        raw = json.loads(row["raw_json"])
        records.append({"conversation_id": row["conversation_id"], "channel": row["channel"],
                        "transcript": raw["transcript"],
                        "normalized_transcript": get_conversation(conn, row["conversation_id"])["transcript"]})
    return records


def _actual_models(conn, records, version_id):
    names, unknown = set(), []
    def inspect(evaluation_id, visited):
        if evaluation_id in visited:
            raise ReferenceUnavailable("В журнале обнаружен цикл происхождения оценок; модель не подтверждена.")
        visited.add(evaluation_id)
        row = conn.execute("SELECT status,original_result_json,final_result_json FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if row is None:
            raise ReferenceUnavailable("Не найдена исходная оценка в цепочке происхождения.")
        calls = conn.execute("SELECT model,error_code,http_status FROM llm_calls WHERE evaluation_id=? AND purpose IN ('evaluation','retry')", (evaluation_id,)).fetchall()
        observed = {c["model"].strip() for c in calls if c["model"] and c["model"].strip() and c["error_code"] is None
                    and c["http_status"] is not None and 200 <= c["http_status"] < 300}
        if not observed and row["status"] != "completed":
            observed = {c["model"].strip() for c in calls if c["model"] and c["model"].strip()}
        result = json.loads(row["final_result_json"] or row["original_result_json"] or "{}")
        plan = result.get("reevaluation") or {}
        if plan.get("baseline_evaluation_id") and plan.get("reused_criterion_ids"):
            observed.update(inspect(plan["baseline_evaluation_id"], visited))
        return observed
    for record in records:
        cid = record["conversation_id"]
        conversation = get_conversation(conn, cid)
        if conversation is None:
            unknown.append(cid)
            continue
        # A fresh API record must correspond to the stored snapshot used for metadata.
        if any(m.get("speaker") != r.get("speaker") or m.get("text") != r.get("text")
               for m, r in zip(conversation["transcript"], record["transcript"])) or len(conversation["transcript"]) != len(record["transcript"]):
            unknown.append(cid)
            continue
        row = conn.execute("SELECT evaluation_id FROM evaluations WHERE conversation_id=? AND checklist_version_id=? AND conversation_revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                           (cid, version_id, conversation["revision"])).fetchone()
        if row is None:
            # A missing evaluation is a legitimate error record; no model is invented.
            continue
        found = inspect(row["evaluation_id"], set())
        if not found:
            unknown.append(cid)
        names.update(found)
    return (sorted(names) if names and not unknown else None), unknown


def audit_file(path, *, split, dataset_source="db", group_id=None, checklist_version_id=None,
               database_path=None):
    """Return a machine-readable PASS/FAIL/НЕ ПРОВЕРЕНО report without any writes."""
    payload, issues = read_submission_file(path)
    report = {"status": "FAIL", "file": str(Path(path).resolve()), "split": split,
              "dataset_source": dataset_source, "group_id": group_id,
              "violations": issues, "not_checked": [], "checks": {"file_parsed": payload is not None}}
    if payload is None:
        return report
    records, models, version = None, None, None
    try:
        settings = load_data_settings()
        reference_database = Path(database_path).expanduser().resolve() if database_path is not None else settings.database_path
        group = group_id or ("DEV" if split == "dev" else settings.group_id)
        report["group_id"] = group
        if split == "prod" and group_id is None and group == "DEV":
            raise ReferenceUnavailable("Для prod укажите --group-id контрольного набора; DEV не подставляется автоматически.")
        if dataset_source == "api":
            if not settings.api_url:
                raise ReferenceUnavailable("DATASET_API_URL пуст: живой запрос не выполнен, сверка с API не проверена.")
            raw = fetch_dataset(settings, group)
            records = raw["data"]["records"]
            # Verify full-source indexing against normalized local messages when available.
            if reference_database.is_file():
                with closing(_read_database(reference_database)) as conn:
                    for record in records:
                        saved = get_conversation(conn, record["conversation_id"])
                        if saved is not None:
                            record["normalized_transcript"] = saved["transcript"]
        elif dataset_source == "db":
            with closing(_read_database(reference_database)) as conn:
                records = _database_records(conn, group)
        else:
            raise ReferenceUnavailable("Неизвестный источник набора.")
        try:
            with closing(_read_database(reference_database)) as conn:
                version = get_checklist(conn, checklist_version_id)["checklist_version_id"]
                models, unknown = _actual_models(conn, records, version)
            if models is None:
                report["not_checked"].append({"code": "MODEL_PROVENANCE_UNAVAILABLE", "message": "Фактическая модель не установлена для всех выбранных оценок.", "conversation_ids": unknown})
        except Exception:
            report["not_checked"].append({"code": "MODEL_PROVENANCE_UNAVAILABLE", "message": "Не удалось подтвердить модель по локальному журналу вызовов."})
    except ReferenceUnavailable as exc:
        report["not_checked"].append({"code": "DATASET_REFERENCE_UNAVAILABLE", "message": str(exc)})
    except Exception as exc:
        report["not_checked"].append({"code": "DATASET_REFERENCE_UNAVAILABLE", "message": "Источник набора недоступен или некорректен. Его содержимое не подменялось.", "error_type": type(exc).__name__})
    issues.extend(inspect_submission(payload, split=split, records=records, expected_models=models))
    report["checks"].update(dataset_reference=records is not None, model_provenance=models is not None,
                            score_method="Independent Decimal calculation for structurally valid ok records",
                            expected_records=65 if split == "dev" else 10,
                            actual_records=len(payload.get("results", [])) if isinstance(payload, dict) and isinstance(payload.get("results"), list) else None,
                            checklist_version_id=version, expected_models=models)
    report["status"] = "FAIL" if issues else "НЕ ПРОВЕРЕНО" if report["not_checked"] else "PASS"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--split", choices=["dev", "prod"])
    parser.add_argument("--dataset-source", choices=["db", "api"], default="db")
    parser.add_argument("--group-id")
    parser.add_argument("--checklist-version-id")
    parser.add_argument("--database-path", type=Path, help="Проверить по указанной локальной БД, открывая её только на чтение.")
    args = parser.parse_args(argv)
    split = args.split or (args.path.parent.name if args.path.parent.name in {"dev", "prod"} else None)
    if split is None:
        parser.error("Укажите --split dev|prod, если путь не находится в папке dev или prod.")
    report = audit_file(args.path, split=split, dataset_source=args.dataset_source,
                        group_id=args.group_id, checklist_version_id=args.checklist_version_id,
                        database_path=args.database_path)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    secret = read_environment().get("MODEL_API_KEY") or ""
    print(output.replace(secret, "[REDACTED]") if secret else output)
    return 1 if report["status"] == "FAIL" else 2 if report["status"] == "НЕ ПРОВЕРЕНО" else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
