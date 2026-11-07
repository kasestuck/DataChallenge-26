"""Local human-labeling exports and agreement metrics; this module never calls an LLM."""
from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from backend.checklist import get_checklist
from backend.config import PROJECT_ROOT, read_environment
from backend.evaluator import get_evaluation


HUMAN_ANSWER = "мой ответ"
HUMAN_COMMENT = "комментарий"
FIELDS = [
    "conversation_id", "language", "channel", "evaluation_id", "checklist_version_id",
    "prompt_hash", "criterion_id", "criterion_name", "allowed_answers", "model_answer",
    "model_valid", "model_answer_source", "evaluation_status", "reasoning", "message_ids",
    "evidence", "checked_range", "selection_reason", HUMAN_ANSWER, HUMAN_COMMENT,
]


class LabelingError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _secrets():
    key = (read_environment().get("MODEL_API_KEY") or "").strip()
    return [key] if key else []


def _redact(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    return value


def _csv_safe(value) -> str:
    text = str(value) if value is not None else ""
    # Prefix every dangerous spreadsheet formula/control prefix, and quote an
    # existing apostrophe too so the transformation remains reversible.
    if text.startswith("'") or (text and (text[0] in "\t\r\n" or text.lstrip().startswith(("=", "+", "-", "@")))):
        return "'" + text
    return text


def _csv_restore(value: str) -> str:
    if value.startswith("'"):
        remainder = value[1:]
        if remainder.startswith("'") or (remainder and (remainder[0] in "\t\r\n" or remainder.lstrip().startswith(("=", "+", "-", "@")))):
            return remainder
    return value


def _markdown(value) -> str:
    return html.escape(str(value or ""), quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def report_path(path: str | Path) -> Path:
    """All transcript-bearing artifacts must remain under the ignored reports tree."""
    allowed = (PROJECT_ROOT / "data" / "reports").resolve()
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target = target.resolve()
    if target != allowed and allowed not in target.parents:
        raise LabelingError("Файлы разметки можно сохранять только внутри data/reports/.")
    return target


def allowed_answers(criterion: dict) -> list[str]:
    result = list(criterion["scale"]) if criterion["type"] == "scale" else ["yes", "no"]
    if criterion.get("allow_na") and criterion["type"] != "flag":
        result.append("na")
    return result


def select_evaluations(conn, *, count=10, group_id="DEV", checklist_version_id=None) -> tuple[dict, list[dict]]:
    if type(count) is not int or count < 1:
        raise LabelingError("count должен быть положительным целым числом.")
    checklist = get_checklist(conn, checklist_version_id)
    candidates = []
    rows = conn.execute("""SELECT c.conversation_id,c.language,c.channel,e.evaluation_id,e.status
        FROM conversations c JOIN evaluations e ON e.evaluation_id=(
          SELECT candidate.evaluation_id FROM evaluations candidate
          WHERE candidate.conversation_id=c.conversation_id AND candidate.checklist_version_id=?
          AND candidate.conversation_revision=c.revision
          AND candidate.status IN ('completed','needs_review','failed')
          ORDER BY candidate.created_at DESC,candidate.rowid DESC LIMIT 1)
        WHERE c.dataset_group_id=? ORDER BY c.conversation_id""",
        (checklist["checklist_version_id"], group_id)).fetchall()
    for raw in rows:
        item = dict(raw)
        history = conn.execute("SELECT status,validation_attempts_json FROM evaluations WHERE conversation_id=?",
                               (item["conversation_id"],)).fetchall()
        historical_review = any(row["status"] == "needs_review" for row in history)
        historical_retry = any(len(json.loads(row["validation_attempts_json"] or "[]")) > 1 for row in history)
        item["selection_reason"] = ["language_channel_coverage"]
        if item["status"] == "needs_review":
            item["selection_reason"].append("current_needs_review")
        if historical_review:
            item["selection_reason"].append("historical_needs_review")
        if historical_retry:
            item["selection_reason"].append("historical_validation_retry")
        item["priority"] = (item["status"] != "needs_review", not historical_review, not historical_retry,
                            item["status"] == "failed", item["conversation_id"])
        candidates.append(item)
    if len(candidates) < count:
        raise LabelingError(f"Для этой версии и группы доступно {len(candidates)} оценённых диалогов, требуется {count}.")
    groups = defaultdict(list)
    for item in sorted(candidates, key=lambda value: value["priority"]):
        groups[(item["language"], item["channel"])].append(item)
    selected = []
    language_order = {"RU": 0, "KZ": 1, "EN": 2}
    buckets = sorted(groups, key=lambda key: (language_order.get(key[0], 99), key[1] != "voice call"))
    # Cover available languages/channels first. Within every bucket, problematic
    # dialogues precede clean ones. Remaining places prioritize unresolved cases.
    for key in buckets:
        if len(selected) == count:
            break
        selected.append(groups[key].pop(0))
    selected_ids = {item["conversation_id"] for item in selected}
    remaining = [item for item in candidates if item["conversation_id"] not in selected_ids]
    while len(selected) < count:
        bucket_counts = Counter((item["language"], item["channel"]) for item in selected)
        remaining.sort(key=lambda item: (*item["priority"][:-1], bucket_counts[(item["language"], item["channel"])], item["conversation_id"]))
        selected.append(remaining.pop(0))
    return checklist, selected


def export_labeling_sheet(conn, *, count=10, group_id="DEV", checklist_version_id=None,
                          output_dir="data/reports/labeling") -> dict:
    destination = report_path(output_dir)
    checklist, chosen = select_evaluations(conn, count=count, group_id=group_id,
                                          checklist_version_id=checklist_version_id)
    secret_values = _secrets()
    rows, documents = [], []
    for item in chosen:
        evaluation = get_evaluation(conn, item["evaluation_id"])
        # Compare with immutable initial output. A reviewer's later decision must
        # never become the model answer for a human-agreement benchmark.
        original = evaluation["original"]
        criteria = {entry["criterion_id"]: entry for entry in original.get("criteria", [])}
        document_rows = []
        for config in checklist["criteria"]:
            if not config.get("active", True):
                continue
            result = criteria.get(config["criterion_id"], {})
            source = result.get("source_kind", result.get("source", "model"))
            answer = result.get("answer")
            valid = bool(result.get("valid", False)) and answer in allowed_answers(config) and source != "reviewer"
            evidence = [{key: entry.get(key) for key in ("message_id", "quote", "speaker")}
                        for entry in result.get("evidence", [])]
            row = {
                "conversation_id": item["conversation_id"], "language": item["language"], "channel": item["channel"],
                "evaluation_id": item["evaluation_id"], "checklist_version_id": checklist["checklist_version_id"],
                "prompt_hash": evaluation.get("prompt_hash") or "", "criterion_id": config["criterion_id"],
                "criterion_name": config["name"], "allowed_answers": _json(allowed_answers(config)),
                "model_answer": answer if valid else "", "model_valid": "true" if valid else "false",
                "model_answer_source": source, "evaluation_status": evaluation["status"],
                "reasoning": result.get("reasoning") or "", "message_ids": _json([e["message_id"] for e in evidence]),
                "evidence": _json(evidence), "checked_range": _json(result.get("checked_range")),
                "selection_reason": _json(item["selection_reason"]), HUMAN_ANSWER: "", HUMAN_COMMENT: "",
            }
            rows.append(row)
            document_rows.append(row)
        documents.append((item, evaluation["conversation_snapshot"]["transcript"], document_rows))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    destination.mkdir(parents=True, exist_ok=True)
    csv_path, md_path = destination / f"labeling-{stamp}.csv", destination / f"labeling-{stamp}.md"
    with csv_path.open("x", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({key: _csv_safe(_redact(str(row[key]), secret_values)) for key in FIELDS} for row in rows)
    lines = ["# Ручная разметка выборки CQA", "",
             "Заполните только колонки «мой ответ» и «комментарий» в CSV. Допустимые ответы указаны в allowed_answers.",
             "Пустой ответ не считается ошибкой и исключается из расчёта согласия. Ответы за проверяющего не заполнены.",
             "Использован слой original; итоговые решения проверяющего не подставляются вместо ответов модели.",
             "Если ответ был перенесён от проверяющего или невалиден, model_answer пуст, model_valid=false.",
             "Это целевая разнообразная выборка с приоритетом спорных случаев, не случайная репрезентативная выборка.",
             "CSV защищён от запуска формул в таблицах префиксом апострофа; скрипт сравнения восстанавливает исходный текст.", "",
             f"Версия чек-листа: `{checklist['checklist_version_id']}`. Диалогов: {len(documents)}. Строк критериев: {len(rows)}.", "",
             "## Правила чек-листа", ""]
    for criterion in checklist["criteria"]:
        if criterion.get("active", True):
            lines.extend([f"### {_markdown(criterion['criterion_id'])}. {_markdown(criterion['name'])}", "",
                          _markdown(_json({key: criterion.get(key) for key in ("description", "type", "weight", "allow_na", "na_condition", "yes_text", "no_text", "scale")})), ""])
    for item, transcript, document_rows in documents:
        lines.extend([f"## {_markdown(item['conversation_id'])} — {_markdown(item['language'])}, {_markdown(item['channel'])}", "",
                      f"Оценка: `{item['evaluation_id']}`. Причины отбора: {_markdown(', '.join(item['selection_reason']))}.", "",
                      "| message_id | timestamp | speaker | Полная реплика |", "| --- | --- | --- | --- |"])
        for message in transcript:
            lines.append("| " + " | ".join(_markdown(message.get(key)) for key in ("message_id", "timestamp", "speaker", "text")) + " |")
        lines.extend(["", "| Критерий | Ответ модели | Объяснение | message_id и цитата | Мой ответ | Комментарий |",
                      "| --- | --- | --- | --- | --- | --- |"])
        for row in document_rows:
            lines.append("| " + " | ".join(_markdown(row[key]) for key in ("criterion_id", "model_answer", "reasoning", "evidence")) + " |  |  |")
        lines.append("")
    with md_path.open("x", encoding="utf-8") as target:
        target.write(_redact("\n".join(lines), secret_values))
    return {"csv_path": str(csv_path), "markdown_path": str(md_path), "conversations": len(documents), "rows": len(rows),
            "checklist_version_id": checklist["checklist_version_id"], "conversation_ids": [item["conversation_id"] for item in chosen],
            "selection": [{key: item[key] for key in ("conversation_id", "language", "channel", "selection_reason")} for item in chosen],
            "human_answers_filled": 0}


def _percent(numerator, denominator):
    if not denominator:
        return None
    return str((Decimal(numerator) / denominator * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def labeling_agreement(csv_path: str | Path) -> dict:
    source = Path(csv_path)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"conversation_id", "evaluation_id", "checklist_version_id", "criterion_id", "allowed_answers",
                    "model_answer", "model_valid", HUMAN_ANSWER, HUMAN_COMMENT}
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not required.issubset(reader.fieldnames):
            raise LabelingError("CSV не содержит обязательных уникальных колонок экспортированной разметки.")
        rows = []
        seen, errors, versions = set(), [], set()
        for line_number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                errors.append(f"Строка {line_number}: неправильное число колонок.")
                continue
            row = {key: _csv_restore(value) for key, value in raw.items()}
            key = (row["conversation_id"], row["criterion_id"])
            if key in seen:
                errors.append(f"Строка {line_number}: повтор диалога и критерия.")
            seen.add(key)
            if any(not row[field].strip() for field in ("conversation_id", "criterion_id", "evaluation_id", "checklist_version_id")):
                errors.append(f"Строка {line_number}: пустые идентификаторы.")
            versions.add(row["checklist_version_id"])
            try:
                allowed = json.loads(row["allowed_answers"])
                if not isinstance(allowed, list) or not allowed or any(not isinstance(a, str) or not a for a in allowed) or len(set(allowed)) != len(allowed):
                    raise ValueError()
            except (ValueError, TypeError):
                errors.append(f"Строка {line_number}: некорректный allowed_answers.")
                continue
            human, model = row[HUMAN_ANSWER].strip(), row["model_answer"].strip()
            if human and human not in allowed:
                errors.append(f"Строка {line_number}: «мой ответ» не входит в allowed_answers.")
            if row["model_valid"] not in {"true", "false"}:
                errors.append(f"Строка {line_number}: model_valid должен быть true или false.")
            if row["model_valid"] == "true" and model not in allowed:
                errors.append(f"Строка {line_number}: недопустимый model_answer.")
            row[HUMAN_ANSWER], row["model_answer"] = human, model
            rows.append(row)
    if len(versions) > 1:
        errors.append("В одном расчёте нельзя смешивать версии чек-листа.")
    if errors:
        raise LabelingError(" ".join(errors))
    if not rows:
        raise LabelingError("CSV не содержит строк разметки.")
    groups = defaultdict(lambda: {"rows": 0, "labeled": 0, "comparable": 0, "matches": 0, "model_unavailable": 0})
    discrepancies = []
    for row in rows:
        group = groups[row["criterion_id"]]
        group["rows"] += 1
        if not row[HUMAN_ANSWER]:
            continue
        group["labeled"] += 1
        if row["model_valid"] != "true":
            group["model_unavailable"] += 1
            discrepancies.append({"conversation_id": row["conversation_id"], "criterion_id": row["criterion_id"],
                                  "model_answer": None, "human_answer": row[HUMAN_ANSWER], "kind": "model_unavailable",
                                  "comment": row[HUMAN_COMMENT]})
            continue
        group["comparable"] += 1
        if row["model_answer"] == row[HUMAN_ANSWER]:
            group["matches"] += 1
        else:
            discrepancies.append({"conversation_id": row["conversation_id"], "criterion_id": row["criterion_id"],
                                  "model_answer": row["model_answer"], "human_answer": row[HUMAN_ANSWER], "kind": "answer_mismatch",
                                  "comment": row[HUMAN_COMMENT]})
    totals = {key: sum(group[key] for group in groups.values()) for key in ("rows", "labeled", "comparable", "matches", "model_unavailable")}
    totals.update(annotation_coverage_percent=_percent(totals["labeled"], totals["rows"]),
                  agreement_percent=_percent(totals["matches"], totals["comparable"]))
    per_criterion = []
    for cid, group in sorted(groups.items()):
        per_criterion.append({"criterion_id": cid, **group, "annotation_coverage_percent": _percent(group["labeled"], group["rows"]),
                              "agreement_percent": _percent(group["matches"], group["comparable"])})
    prompt_hashes = Counter(row.get("prompt_hash") or "unknown" for row in rows)
    warnings = []
    if "unknown" in prompt_hashes:
        warnings.append("Для части строк хэш промпта неизвестен: это оценки до введения версионирования промпта.")
    if len(prompt_hashes) > 1:
        warnings.append("В выборке несколько версий промпта; общий показатель описывает именно эту смешанную выборку.")
    report = {"metric": "согласие с ручной разметкой на выборке", "checklist_version_id": next(iter(versions)),
              "overall": totals, "per_criterion": per_criterion, "disagreements": discrepancies,
              "prompt_hash_rows": dict(prompt_hashes), "warnings": warnings,
              "limitations": "Пустые ответы исключены. Невалидные ответы модели показаны отдельно. Выборка не случайная; метрика не обобщается на весь набор."}
    return json.loads(_redact(_json(report), _secrets()))
