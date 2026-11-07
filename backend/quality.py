"""Descriptive quality diagnostics; these metrics are not expert-label accuracy."""
from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal
from itertools import combinations

from backend.evaluator import get_evaluation
from backend.scoring import calculate_scores


def answer_distributions(evaluations):
    groups = {}
    for label in ("RU", "KZ", "EN", "voice_call"):
        selected = [e for e in evaluations if (e["conversation_snapshot"]["channel"] == "voice call"
                    if label == "voice_call" else e["conversation_snapshot"]["language"] == label)]
        criteria = {}
        ids = sorted({c["criterion_id"] for e in selected for c in e.get("criteria", [])})
        for cid in ids:
            answers = []
            for evaluation in selected:
                row = next((c for c in evaluation.get("criteria", []) if c["criterion_id"] == cid), None)
                if row and row.get("valid") and row.get("answer") is not None:
                    answers.append(row["answer"])
            counts = dict(sorted(Counter(answers).items()))
            criteria[cid] = {"answers": counts, "valid_count": len(answers),
                             "missing_or_invalid": len(selected) - len(answers),
                             "percent_of_valid": {key: round(value / len(answers) * 100, 2) for key, value in counts.items()}}
        groups[label] = {"dialogues": len(selected), "criteria": criteria,
                         "total_distribution": dict(sorted(Counter(str(e.get("total")) for e in selected).items()))}
    anomalies = []
    for label, group in groups.items():
        scored_totals = {total: count for total, count in group["total_distribution"].items() if total != "None"}
        if scored_totals:
            total, count = max(scored_totals.items(), key=lambda item: item[1])
            size = sum(scored_totals.values())
            if count / size > .5:
                anomalies.append({"kind": "dominant_total", "group": label, "total": total,
                                  "percent": round(count / size * 100, 2), "sample_size": size,
                                  "note": "Один итоговый балл встречается более чем у половины оценённых диалогов; проверьте состав набора."})
        identity = group["criteria"].get("03", {})
        if identity.get("valid_count"):
            share = identity["percent_of_valid"].get("na", 0)
            if share > 80 or share < 10:
                anomalies.append({"kind": "identification_na_share", "group": label, "criterion_id": "03",
                                  "percent": share, "sample_size": identity["valid_count"],
                                  "note": "Эвристика: доля N/A выше 80% либо ниже 10%; проверьте состав запросов, это не доказательство ошибки."})
        for cid, criterion in group["criteria"].items():
            if criterion["valid_count"]:
                answer, share = max(criterion["percent_of_valid"].items(), key=lambda item: item[1])
                if share > 85:
                    anomalies.append({"kind": "dominant_answer", "group": label, "criterion_id": cid,
                                      "answer": answer, "percent": share, "sample_size": criterion["valid_count"],
                                      "note": "Один ответ превышает 85%; однородность может объясняться набором."})
    for left, right in combinations(("RU", "KZ", "EN"), 2):
        for cid in sorted(set(groups[left]["criteria"]) & set(groups[right]["criteria"])):
            a, b = groups[left]["criteria"][cid], groups[right]["criteria"][cid]
            if not a["valid_count"] or not b["valid_count"]:
                continue
            for answer in sorted(set(a["answers"]) | set(b["answers"])):
                difference = abs(a["percent_of_valid"].get(answer, 0) - b["percent_of_valid"].get(answer, 0))
                if difference > 35:
                    anomalies.append({"kind": "language_gap", "groups": [left, right], "criterion_id": cid,
                                      "answer": answer, "percentage_points": round(difference, 2),
                                      "sample_sizes": [a["valid_count"], b["valid_count"]],
                                      "note": "Разница выше 35 п.п.; темы и каналы наборов могут различаться, причинный языковой перекос не установлен."})
    return {"groups": groups, "anomalies": anomalies,
            "note": "Распределения ответов и эвристики, не точность. voice_call — пересекающийся с языками срез."}


def repeatability(runs, conversation_ids):
    if len(runs) != 3:
        raise ValueError("Для проверки повторяемости нужны ровно три прогона.")
    indexes = []
    for run in runs:
        index = {row["conversation_id"]: row for row in run}
        if len(index) != len(run):
            raise ValueError("В прогоне повторяется conversation_id.")
        indexes.append(index)
    criteria_ids = sorted({c["criterion_id"] for run in runs for e in run for c in e.get("criteria", [])})
    stats, differences = {}, []
    for cid in criteria_ids:
        complete, unanimous, pair_count, pair_equal = 0, 0, 0, 0
        for conversation_id in conversation_ids:
            values = []
            for index in indexes:
                row = next((c for c in index.get(conversation_id, {}).get("criteria", []) if c["criterion_id"] == cid), None)
                values.append(row["answer"] if row and row.get("valid") else None)
            if all(value is not None for value in values):
                complete += 1
                unanimous += len(set(values)) == 1
            for left, right in combinations(values, 2):
                if left is not None and right is not None:
                    pair_count += 1
                    pair_equal += left == right
            if None in values or len(set(values)) != 1:
                differences.append({"conversation_id": conversation_id, "criterion_id": cid, "answers": values,
                                    "missing_or_invalid": any(value is None for value in values)})
        stats[cid] = {"requested_dialogues": len(conversation_ids), "complete_triplets": complete,
                      "unanimous_triplets": unanimous, "unanimous_percent": round(unanimous / complete * 100, 2) if complete else None,
                      "coverage_percent": round(complete / len(conversation_ids) * 100, 2) if conversation_ids else None,
                      "valid_pairs": pair_count, "matching_pairs": pair_equal,
                      "pairwise_percent": round(pair_equal / pair_count * 100, 2) if pair_count else None}
    totals = {key: sum(s[key] for s in stats.values()) for key in ("complete_triplets", "unanimous_triplets", "valid_pairs", "matching_pairs")}
    totals["unanimous_percent"] = round(totals["unanimous_triplets"] / totals["complete_triplets"] * 100, 2) if totals["complete_triplets"] else None
    totals["pairwise_percent"] = round(totals["matching_pairs"] / totals["valid_pairs"] * 100, 2) if totals["valid_pairs"] else None
    return {"criteria": stats, "overall": totals, "differences": differences,
            "note": "Повторяемость ответов одной модели; не сравнение с экспертной разметкой. Невалидные ответы не считаются совпадениями."}


def latest_completed(conn, group_id, version_id):
    result = []
    rows = conn.execute("SELECT c.conversation_id, (SELECT e.evaluation_id FROM evaluations e WHERE e.conversation_id=c.conversation_id "
                        "AND e.checklist_version_id=? AND e.status='completed' AND e.conversation_revision=c.revision "
                        "ORDER BY e.created_at DESC,e.rowid DESC LIMIT 1) AS evaluation_id FROM conversations c "
                        "WHERE c.dataset_group_id=? ORDER BY c.conversation_id", (version_id, group_id)).fetchall()
    for row in rows:
        if row["evaluation_id"]:
            result.append(get_evaluation(conn, row["evaluation_id"]))
    return result


def select_diverse(evaluations, count=8, *, identification=False):
    chosen = []
    available = sorted(evaluations, key=lambda e: (sum(len(m["text"]) for m in e["conversation_snapshot"]["transcript"]), e["conversation_id"]))
    def pick(predicate, description):
        row = next((e for e in available if e["conversation_id"] not in chosen and predicate(e)), None)
        if row is None:
            raise ValueError(f"Нет подходящей завершённой оценки для группы: {description}.")
        chosen.append(row["conversation_id"])
    if identification:
        pick(lambda e: any(c["criterion_id"] == "03" and c.get("valid") and c.get("answer") in {"yes", "no"}
                           for c in e["criteria"]), "применимая идентификация")
    for language in ("RU", "KZ", "EN"):
        pick(lambda e, language=language: e["conversation_snapshot"]["language"] == language
             and e["conversation_snapshot"]["channel"] == "agent chat", language)
    pick(lambda e: e["conversation_snapshot"]["channel"] == "voice call", "voice call")
    for row in available:
        if len(chosen) >= count:
            break
        if row["conversation_id"] not in chosen:
            chosen.append(row["conversation_id"])
    if len(chosen) != count:
        raise ValueError(f"Нужно {count} разных диалогов; доступно {len(chosen)}.")
    return chosen


def jargon_checklist(original, *, scale=False):
    result = copy.deepcopy(original)
    old = next((c for c in result["criteria"] if c["criterion_id"] in {"05", "09"}), None)
    if old is None:
        raise ValueError("Не найден критерий 05 или 09 для демонстрационной замены.")
    old.update(criterion_id="09", name="Оператор не использовал жаргон", type="scale" if scale else "yes_no",
               description="Профессиональные сокращения и жаргон должны быть объяснены простыми словами. Общеупотребительные названия интерфейса сами по себе не являются жаргоном.",
               allow_na=False, na_condition=None, scored=True, active=True)
    if scale:
        old.update(yes_text=None, no_text=None, scale={
            "5": "Речь понятна: жаргона нет либо все специальные выражения объяснены.",
            "4": "Одно необъяснённое специальное выражение, общий смысл понятен.",
            "3": "Несколько необъяснённых выражений, основной смысл можно понять.",
            "2": "Жаргон заметно затрудняет понимание существенной части ответа.",
            "1": "Большая часть ответа непонятна без профессиональной подготовки.",
            "0": "Ответ состоит из необъяснённого жаргона и не даёт понятной инструкции.",
        })
    else:
        old.update(yes_text="Необъяснённого жаргона нет; специальные выражения объяснены простыми словами.",
                   no_text="В ответах есть необъяснённый профессиональный жаргон.", scale={})
    return result


def verify_partial_result(evaluation, baseline):
    if evaluation["status"] != "completed":
        return {"verified": False, "reason": "evaluation_not_completed"}
    if evaluation["reevaluation"]["evaluated_criterion_ids"] != ["09"]:
        raise ValueError("В демонстрации замены модель должна оценить только новый критерий 09.")
    scores = calculate_scores(evaluation["checklist"], {c["criterion_id"]: c["answer"] for c in evaluation["criteria"]})
    if (str(scores["total"]) if scores["total"] is not None else None) != evaluation["total"]:
        raise ValueError("Пересчитанный итог не совпадает с сохранённым.")
    previous = {c["criterion_id"]: c for c in baseline["criteria"]}
    unchanged = evaluation["reevaluation"]["reused_criterion_ids"]
    for row in evaluation["criteria"]:
        if row["criterion_id"] in unchanged:
            if any(row.get(field) != previous[row["criterion_id"]].get(field)
                   for field in ("answer", "reasoning", "evidence", "checked_range")):
                raise ValueError("Перенесённый ответ отличается от базовой оценки.")
    return {"verified": True, "reused_answers_unchanged": len(unchanged), "total_recalculated": evaluation["total"],
            "evaluated_criterion_ids": evaluation["reevaluation"]["evaluated_criterion_ids"]}
