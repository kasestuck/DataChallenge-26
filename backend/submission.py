"""Export stored CQA evaluations into the organizers' JSON 1.0 submission format.

This adapter never evaluates a dialogue or repairs an answer. Its fixed mapping
belongs to the external submission contract; the evaluator remains config-driven.
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from backend.checklist import get_checklist
from backend.dataset import get_conversation
from backend.evaluator import get_evaluation
from backend.scoring import calculate_scores
from backend.validation import normalize_text


# internal id, external id, type, weight, nullable, permitted scale values
RULES = (
    ("01", "contact", "yes_no", 10, False, ()),
    ("02", "discovery", "yes_no", 15, True, ()),
    ("03", "identification", "yes_no", 20, True, ()),
    ("04", "consultation", "scale", 20, True, (0, 5, 10, 15, 20)),
    ("05", "empathy", "yes_no", 15, False, ()),
    ("06", "dialogue", "scale", 15, False, (0, 4, 7, 10)),
    ("07", "closure", "yes_no", 5, True, ()),
    ("08", "critical", "flag", 0, False, ()),
)


class SubmissionError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise SubmissionError(code, message)


def _number(value):
    if isinstance(value, bool):
        raise SubmissionError("INVALID_NUMBER", "Ожидалось конечное число, а не логическое значение.")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise SubmissionError("INVALID_NUMBER", "Ожидалось конечное число.") from None
    _require(result.is_finite(), "INVALID_NUMBER", "Ожидалось конечное число.")
    return result


def validate_official_checklist(checklist):
    """Reject demo/reweighted configurations that cannot satisfy JSON 1.0."""
    active = [c for c in checklist["criteria"] if c.get("active", True)]
    _require(len(active) == 8 and {c["criterion_id"] for c in active} == {r[0] for r in RULES},
             "SUBMISSION_CHECKLIST_MISMATCH", "Для сдачи нужны ровно восемь исходных критериев 01–08.")
    by_id = {c["criterion_id"]: c for c in active}
    for cid, _, kind, weight, nullable, scale in RULES:
        c = by_id[cid]
        valid = (c["type"] == kind and _number(c["weight"]) == weight
                 and c.get("allow_na", False) is nullable
                 and c.get("scored", True) is (kind != "flag"))
        if scale:
            valid = valid and set(c.get("scale", {})) == {str(v) for v in scale}
        _require(valid, "SUBMISSION_CHECKLIST_MISMATCH",
                 "Типы, веса, N/A или ступени чек-листа не соответствуют формату сдачи 1.0.")


def validate_group_records(records, split):
    """Check the received group before making any billable evaluation requests."""
    _require(split in {"dev", "prod"}, "INVALID_SPLIT", "Набор сдачи должен быть dev или prod.")
    _require(isinstance(records, list) and all(isinstance(r, dict) for r in records),
             "INVALID_CONVERSATION_IDS", "Ожидается массив записей исходного набора.")
    ids = [r.get("conversation_id") for r in records]
    _require(all(isinstance(cid, str) and cid for cid in ids) and len(ids) == len(set(ids)),
             "INVALID_CONVERSATION_IDS", "Список ID некорректен или содержит повторы.")
    _require(len(ids) == (65 if split == "dev" else 10), "SUBMISSION_COUNT_MISMATCH",
             "Для DEV требуются 65 записей, для PROD — ровно 10; неполный файл не создан.")
    expected = {"agent chat": 60, "voice call": 5} if split == "dev" else {"agent chat": 10}
    _require(dict(Counter(r.get("channel") for r in records)) == expected, "SUBMISSION_CHANNEL_MISMATCH",
             "Состав каналов должен быть DEV: 60 чатов и 5 звонков; PROD: 10 чатов.")


def _answer_value(answer, kind, nullable, scale):
    if answer == "na" and nullable:
        return None
    if kind in {"yes_no", "flag"} and answer in {"yes", "no"}:
        return answer == "yes"
    if kind == "scale" and isinstance(answer, str) and answer in {str(v) for v in scale}:
        return int(answer)
    raise SubmissionError("INVALID_STORED_ANSWER", "Сохранённый ответ не является допустимым значением критерия.")


def _evidence_turns(row, transcript):
    positions = {m["message_id"]: index for index, m in enumerate(transcript, 1)}
    messages = {m["message_id"]: m for m in transcript}
    _require(len(positions) == len(transcript), "INVALID_TRANSCRIPT", "В транскрипте повторяются message_id.")
    selected = set()
    evidence = row.get("evidence", [])
    _require(isinstance(evidence, list), "INVALID_EVIDENCE", "Доказательства не являются массивом.")
    for item in evidence:
        mid = item.get("message_id") if isinstance(item, dict) else None
        _require(mid in positions, "INVALID_EVIDENCE", "Реплика доказательства отсутствует в исходном транскрипте.")
        quote = item.get("quote")
        _require(isinstance(quote, str) and quote.strip()
                 and normalize_text(quote) in normalize_text(messages[mid]["text"]),
                 "INVALID_EVIDENCE", "Цитата доказательства не найдена в указанной реплике.")
        selected.add(positions[mid])
    checked = row.get("checked_range")
    if checked is not None:
        _require(isinstance(checked, dict), "INVALID_EVIDENCE", "Некорректен диапазон проверки.")
        start, end = positions.get(checked.get("from")), positions.get(checked.get("to"))
        _require(start is not None and end is not None and start <= end,
                 "INVALID_EVIDENCE", "Некорректен диапазон проверки.")
        selected.update(range(start, end + 1))
    return sorted(selected)


def _models_for_evaluation(conn, evaluation, seen=None):
    seen = set() if seen is None else seen
    eid = evaluation["evaluation_id"]
    _require(eid not in seen, "INVALID_PROVENANCE", "Обнаружен цикл ссылок на исходные оценки.")
    seen.add(eid)
    calls = conn.execute("SELECT model,error_code,http_status FROM llm_calls WHERE evaluation_id=? "
                         "AND purpose IN ('evaluation','retry')", (eid,)).fetchall()
    names = {r[0].strip() for r in calls if r[0] and r[0].strip() and r[1] is None
             and r[2] is not None and 200 <= r[2] < 300}
    if not names and evaluation["status"] != "completed":
        # For an error record only, the audit can establish the attempted model.
        # This does not imply it returned a successful evaluation.
        names = {r[0].strip() for r in calls if r[0] and r[0].strip()}
    plan = evaluation.get("reevaluation") or {}
    baseline_id = plan.get("baseline_evaluation_id")
    if baseline_id and plan.get("reused_criterion_ids"):
        baseline = get_evaluation(conn, baseline_id)
        _require(baseline["conversation_id"] == evaluation["conversation_id"]
                 and baseline["conversation_snapshot"]["transcript"] == evaluation["conversation_snapshot"]["transcript"],
                 "INVALID_PROVENANCE", "Исходная оценка относится к другому транскрипту.")
        names.update(_models_for_evaluation(conn, baseline, seen))
    return names


def _export_ok(evaluation, conversation, checklist):
    snapshot = evaluation["conversation_snapshot"]
    _require(snapshot.get("conversation_id") == conversation["conversation_id"]
             and snapshot.get("channel") == conversation["channel"]
             and snapshot.get("language") == conversation["language"]
             and snapshot.get("transcript") == conversation["transcript"],
             "STALE_EVALUATION", "Оценка не соответствует текущему исходному транскрипту.")
    rows = evaluation.get("criteria", [])
    _require(len(rows) == 8 and {c.get("criterion_id") for c in rows} == {r[0] for r in RULES},
             "INVALID_STORED_CRITERIA", "В сохранённой оценке отсутствуют критерии или присутствуют дубли.")
    by_id = {c["criterion_id"]: c for c in rows}
    exported, answers = [], {}
    for cid, external, kind, _, nullable, scale in RULES:
        row = by_id[cid]
        _require(row.get("valid") is True, "INVALID_STORED_CRITERIA", "Сохранённый критерий не прошёл валидацию.")
        reasoning = row.get("reasoning")
        _require(isinstance(reasoning, str) and reasoning.strip(), "INVALID_RATIONALE", "У критерия отсутствует объяснение.")
        answers[cid] = row.get("answer")
        exported.append({"criterion_id": external, "value": _answer_value(answers[cid], kind, nullable, scale),
                         "rationale": reasoning, "evidence_turns": _evidence_turns(row, conversation["transcript"])})
    scores = calculate_scores(checklist, answers)
    _require(scores["total"] is not None and _number(evaluation.get("total")) == scores["total"]
             and _number(evaluation.get("final_total")) == scores["total"]
             and type(evaluation.get("gross_violation")) is bool
             and evaluation["gross_violation"] == scores["gross_violation"],
             "SCORE_MISMATCH", "Сохранённый итог или флаг не совпадает с независимым пересчётом.")
    return {"conversation_id": conversation["conversation_id"], "status": "ok", "criteria": exported,
            "score": {"final_score": float(scores["total"]), "critical_flag": scores["gross_violation"]}}


def _error_record(cid, message):
    return {"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": message}


def build_submission(conn, *, group_id, split, team_name, checklist_version_id=None, conversation_ids=None):
    """Read stored results only. Failed/missing evaluations remain explicit errors."""
    _require(split in {"dev", "prod"}, "INVALID_SPLIT", "Набор сдачи должен быть dev или prod.")
    _require(isinstance(team_name, str) and bool(team_name.strip()), "TEAM_NAME_REQUIRED", "Укажите название команды.")
    _require(isinstance(group_id, str) and bool(group_id.strip()), "INVALID_GROUP_ID", "Укажите group_id.")
    checklist = get_checklist(conn, checklist_version_id)
    validate_official_checklist(checklist)
    version = checklist["checklist_version_id"]
    group_rows = conn.execute("SELECT conversation_id FROM conversations WHERE dataset_group_id=? ORDER BY conversation_id", (group_id,)).fetchall()
    available = {r[0] for r in group_rows}
    ids = list(conversation_ids) if conversation_ids is not None else sorted(available)
    _require(all(isinstance(cid, str) and cid for cid in ids) and len(ids) == len(set(ids)),
             "INVALID_CONVERSATION_IDS", "Список ID пуст, некорректен или содержит повторы.")
    _require(set(ids).issubset(available), "GROUP_MEMBERSHIP_MISMATCH", "Не все ID принадлежат загруженному набору.")
    _require(len(ids) == (65 if split == "dev" else 10), "SUBMISSION_COUNT_MISMATCH",
             "Для DEV требуются 65 записей, для PROD — ровно 10; неполный файл не создан.")
    conversations = [get_conversation(conn, cid) for cid in sorted(ids)]
    validate_group_records(conversations, split)
    results, model_names, hashes, selected = [], set(), Counter(), []
    for conversation in conversations:
        cid = conversation["conversation_id"]
        stored = conn.execute(
            "SELECT evaluation_id FROM evaluations WHERE conversation_id=? AND checklist_version_id=? "
            "AND conversation_revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (cid, version, conversation["revision"])).fetchone()
        if stored is None:
            results.append(_error_record(cid, "Нет оценки текущей версии транскрипта по выбранному чек-листу."))
            continue
        evaluation = get_evaluation(conn, stored[0])
        selected.append(stored[0])
        hashes[evaluation.get("prompt_hash") or "legacy_unknown"] += 1
        names = _models_for_evaluation(conn, evaluation)
        model_names.update(names)
        if evaluation["status"] != "completed":
            messages = {"needs_review": "Ответ модели требует проверки и не может быть сдан как успешная оценка.",
                        "failed": "Оценить диалог не удалось; сохранена ошибка обработки.",
                        "pending": "Оценка ещё не начата.", "running": "Оценка ещё не завершена."}
            results.append(_error_record(cid, messages.get(evaluation["status"], "Нет завершённой оценки.")))
            continue
        if not names:
            results.append(_error_record(cid, "Для сохранённой оценки не установлено фактически использованное имя модели."))
            continue
        try:
            results.append(_export_ok(evaluation, conversation, checklist))
        except (SubmissionError, ValueError, KeyError, TypeError):
            # Domain messages are deliberately bounded; no raw provider or source text.
            results.append(_error_record(cid, "Сохранённая оценка не прошла проверку структуры, доказательств или пересчёта."))
    _require(model_names, "MODEL_PROVENANCE_UNKNOWN", "Нельзя установить имя фактически использованной модели из журнала вызовов.")
    payload = {"schema_version": "1.0", "team_name": team_name.strip(),
               "model": {"mode": "external_api", "name": ", ".join(sorted(model_names))}, "results": results}
    lengths = {c["conversation_id"]: len(c["transcript"]) for c in conversations}
    validate_submission(payload, expected_ids=ids, transcript_lengths=lengths)
    counts = Counter(r["status"] for r in results)
    metadata = {"group_id": group_id, "split": split, "checklist_version_id": version,
                "count": len(results), "ok": counts["ok"], "error": counts["error"],
                "counts": {"ok": counts["ok"], "error": counts["error"]},
                "models": sorted(model_names), "evaluation_ids": selected,
                "model_name_basis": "Имена из успешных вызовов; для error без успешных вызовов — имя попытки из журнала.",
                "prompt_hashes": dict(hashes), "expected_ids": sorted(ids), "transcript_lengths": lengths}
    return payload, metadata


def validate_submission(payload, *, expected_ids, transcript_lengths):
    """Strict JSON schema/type/coverage/evidence validation plus independent arithmetic."""
    _require(isinstance(payload, dict) and set(payload) == {"schema_version", "team_name", "model", "results"},
             "INVALID_SUBMISSION", "Некорректные поля корневого объекта JSON.")
    _require(payload["schema_version"] == "1.0" and isinstance(payload["team_name"], str)
             and bool(payload["team_name"].strip()), "INVALID_SUBMISSION", "Некорректна версия формата или название команды.")
    model = payload["model"]
    _require(isinstance(model, dict) and set(model) == {"mode", "name"}
             and model["mode"] in {"local", "external_api", "hybrid"}
             and isinstance(model["name"], str) and bool(model["name"].strip()),
             "INVALID_SUBMISSION", "Некорректны сведения о модели.")
    results = payload["results"]
    _require(isinstance(results, list) and all(isinstance(r, dict) for r in results),
             "INVALID_SUBMISSION", "results должен содержать объекты результатов.")
    ids = [r.get("conversation_id") for r in results]
    expected = list(expected_ids)
    _require(all(isinstance(cid, str) and cid for cid in ids) and len(set(ids)) == len(ids)
             and len(set(expected)) == len(expected) and set(ids) == set(expected),
             "INVALID_SUBMISSION", "ID результатов не совпадают с исходным набором или содержат повторы.")
    rules = {r[1]: r for r in RULES}
    for result in results:
        status = result.get("status")
        required = {"conversation_id", "status", "criteria", "score"}
        if status == "error":
            _require(set(result) == required | {"error"} and result["criteria"] == [] and result["score"] is None
                     and isinstance(result["error"], str) and bool(result["error"].strip()),
                     "INVALID_SUBMISSION", "Ошибка должна содержать пустые criteria, score=null и причину.")
            continue
        _require(status == "ok" and set(result) == required, "INVALID_SUBMISSION", "Некорректный статус или поля результата.")
        criteria = result["criteria"]
        _require(isinstance(criteria, list) and len(criteria) == 8 and all(isinstance(c, dict) for c in criteria),
                 "INVALID_SUBMISSION", "Успешная оценка требует восемь критериев.")
        cids = [c.get("criterion_id") for c in criteria]
        _require(all(isinstance(c, str) for c in cids) and len(set(cids)) == 8 and set(cids) == set(rules),
                 "INVALID_SUBMISSION", "Коды критериев не соответствуют формату 1.0.")
        earned, weight_sum, critical = Decimal(0), Decimal(0), False
        for criterion in criteria:
            _require(set(criterion) == {"criterion_id", "value", "rationale", "evidence_turns"}
                     and isinstance(criterion["rationale"], str) and bool(criterion["rationale"].strip()),
                     "INVALID_SUBMISSION", "Некорректны поля или объяснение критерия.")
            _, _, kind, weight, nullable, steps = rules[criterion["criterion_id"]]
            value, turns = criterion["value"], criterion["evidence_turns"]
            valid_value = value is None and nullable
            if value is not None:
                valid_value = type(value) is bool if kind in {"yes_no", "flag"} else type(value) is int and value in steps
            _require(valid_value, "INVALID_SUBMISSION", "Некорректный тип или значение критерия.")
            limit = transcript_lengths.get(result["conversation_id"])
            _require(type(limit) is int and limit >= 0 and isinstance(turns, list)
                     and all(type(t) is int and 1 <= t <= limit for t in turns)
                     and len(turns) == len(set(turns)),
                     "INVALID_SUBMISSION", "Номер доказательства выходит за пределы исходного транскрипта или повторяется.")
            if kind == "flag":
                critical = value
            elif value is not None:
                weight_sum += weight
                earned += Decimal(weight) * (Decimal(value) / max(steps) if steps else int(value))
        score = result["score"]
        _require(isinstance(score, dict) and set(score) == {"final_score", "critical_flag"}
                 and type(score["final_score"]) in {int, float}
                 and type(score["critical_flag"]) is bool and score["critical_flag"] == critical,
                 "INVALID_SUBMISSION", "Некорректные поля или типы итогового балла и флага.")
        expected_score = (earned / weight_sum * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        number = _number(score["final_score"])
        _require(0 <= number <= 100 and number == expected_score,
                 "INVALID_SUBMISSION", "Итоговый балл не совпадает с независимым пересчётом.")
    # Also reject non-JSON values/NaN that Python's default encoder may accept.
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise SubmissionError("INVALID_SUBMISSION", "Объект содержит значения вне формата JSON.") from None
    return True
