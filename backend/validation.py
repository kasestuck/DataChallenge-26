"""Structural and transcript-grounding validation of untrusted model output.

This module does not infer missing answers or certify semantic QA correctness.
For N/A, a deterministic reference is required: reasoning quotes the full
configured na_condition (normalization allows typography/case/spacing changes).
Whether that condition is actually true remains an evaluator/reviewer judgment.
"""

import json
import re
import unicodedata
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


class ModelJSONError(ValueError):
    """The model did not return one unambiguous JSON object."""


_TYPOGRAPHY = str.maketrans({
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "`": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
})


def normalize_text(text: str) -> str:
    """Normalize comparison text, never the text saved or displayed as evidence."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY).casefold().split())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelJSONError("JSON содержит повторяющиеся ключи; верните однозначный объект.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ModelJSONError("JSON содержит NaN/Infinity; допустим только стандартный JSON.")


def parse_model_json(content: str) -> dict:
    """Accept raw JSON or exactly one enclosing plain/json Markdown fence."""
    if not isinstance(content, str) or not content.strip():
        raise ModelJSONError("Пустой ответ; требуется JSON-объект.")
    text = content.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", text, flags=re.IGNORECASE)
        if match is None:
            raise ModelJSONError("Некорректное Markdown-ограждение; верните только JSON-объект.")
        text = match.group(1).strip()
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ModelJSONError:
        raise
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise ModelJSONError("Ответ не является валидным JSON-объектом без текста вокруг.") from None
    if not isinstance(payload, dict):
        raise ModelJSONError("Верхний уровень ответа должен быть JSON-объектом.")
    return payload


def validate_response(payload: dict, conversation: dict, checklist: dict) -> list[dict]:
    """Return criterion-scoped errors; criterion_id=None means an envelope error.

    Every scale step below its configured maximum is treated as a low step and
    needs checked_range. A positive scale step still needs evidence. An omitted
    action (no or zero scale) may have no evidence only with a valid checked_range.
    """
    errors: list[dict] = []

    def error(cid: str | None, code: str, message: str) -> None:
        errors.append({"criterion_id": cid, "code": code, "message": message})

    if not isinstance(checklist, dict) or not isinstance(checklist.get("criteria"), list):
        error(None, "INVALID_CHECKLIST", "Чек-лист должен содержать массив criteria.")
        return errors
    if any(not isinstance(c, dict) or not isinstance(c.get("criterion_id"), str) for c in checklist["criteria"]):
        error(None, "INVALID_CHECKLIST", "Критерий должен быть объектом со строковым criterion_id.")
        return errors
    criteria = [c for c in checklist["criteria"] if c.get("active", True)]
    expected = {c["criterion_id"]: c for c in criteria}
    if len(expected) != len(criteria):
        error(None, "INVALID_CHECKLIST", "В активном чек-листе повторяются criterion_id.")
        return errors
    if not isinstance(conversation, dict) or not isinstance(conversation.get("transcript"), list):
        error(None, "INVALID_TRANSCRIPT", "Диалог должен содержать массив transcript.")
        return errors
    messages = {}
    positions = {}
    for position, message in enumerate(conversation["transcript"]):
        if (not isinstance(message, dict) or not isinstance(message.get("message_id"), str)
                or not isinstance(message.get("text"), str) or message["message_id"] in messages):
            error(None, "INVALID_TRANSCRIPT", "Реплики должны содержать уникальный message_id и строковый text.")
            return errors
        messages[message["message_id"]] = message
        positions[message["message_id"]] = position
    if not isinstance(payload, dict):
        error(None, "INVALID_RESPONSE", "Ответ должен быть JSON-объектом.")
        return errors
    if payload.get("conversation_id") != conversation.get("conversation_id"):
        error(None, "CONVERSATION_ID_MISMATCH", "conversation_id ответа должен совпадать с ID оцениваемого диалога.")
    rows = payload.get("criteria")
    if not isinstance(rows, list):
        error(None, "CRITERIA_NOT_LIST", "criteria должен быть массивом результатов.")
        rows = []
    valid_ids = [r.get("criterion_id") for r in rows if isinstance(r, dict) and isinstance(r.get("criterion_id"), str)]
    counts = Counter(valid_ids)
    for cid in expected:
        if counts[cid] == 0:
            error(cid, "MISSING_CRITERION", f"Отсутствует активный критерий {cid}.")
        elif counts[cid] > 1:
            error(cid, "DUPLICATE_CRITERION", f"Критерий {cid} должен присутствовать ровно один раз.")
    for cid in counts.keys() - expected.keys():
        error(cid, "EXTRA_CRITERION", f"Критерий {cid} отсутствует среди активных критериев чек-листа.")

    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("criterion_id"), str):
            error(None, "INVALID_CRITERION", "Результат критерия должен быть объектом со строковым criterion_id.")
            continue
        cid = row["criterion_id"]
        if cid not in expected:
            continue
        criterion = expected[cid]
        kind = criterion.get("type")
        answer = row.get("answer")
        is_string = isinstance(answer, str)
        scale_value = None
        scale_max = None
        scale = criterion.get("scale")
        if kind == "scale" and isinstance(scale, dict) and scale:
            try:
                numerical = [Decimal(str(k)) for k in scale]
                if any(not n.is_finite() or n < 0 for n in numerical):
                    raise ValueError()
                scale_max = max(numerical)
                if is_string and answer in scale:
                    scale_value = Decimal(answer)
            except (InvalidOperation, ValueError, TypeError):
                error(cid, "INVALID_SCALE_CONFIG", "Шкала чек-листа содержит некорректные числовые ступени.")
        if not is_string:
            error(cid, "INVALID_ANSWER", "answer должен быть строкой с разрешённым значением.")
        elif answer == "na":
            if kind == "flag" or not criterion.get("allow_na", False):
                error(cid, "NA_NOT_ALLOWED", "N/A запрещён для этого критерия.")
        elif kind in ("yes_no", "flag"):
            if answer not in ("yes", "no"):
                error(cid, "INVALID_ANSWER", "Допустимые ответы: yes или no; na только если разрешён.")
        elif kind == "scale":
            if not isinstance(scale, dict) or answer not in scale:
                error(cid, "INVALID_SCALE_STEP", "answer должен точно совпадать с одним из строковых ключей scale.")
        else:
            error(cid, "INVALID_CRITERION_TYPE", "В чек-листе указан неподдерживаемый тип критерия.")

        reasoning = row.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            error(cid, "REASONING_REQUIRED", "Добавьте непустое объяснение reasoning.")
        elif answer == "na":
            condition = criterion.get("na_condition")
            if (not isinstance(condition, str) or not condition.strip()
                    or normalize_text(condition) not in normalize_text(reasoning)):
                error(cid, "NA_CONDITION_REFERENCE_REQUIRED", "При na процитируйте в reasoning полный na_condition и объясните его применимость.")

        checked_range = row.get("checked_range")
        range_valid = False
        if checked_range is not None:
            if not isinstance(checked_range, dict):
                error(cid, "INVALID_CHECKED_RANGE", "checked_range должен быть объектом с from/to или null.")
            else:
                start, end = checked_range.get("from"), checked_range.get("to")
                if (not isinstance(start, str) or not isinstance(end, str)
                        or start not in messages or end not in messages):
                    error(cid, "UNKNOWN_RANGE_MESSAGE", "from и to должны ссылаться на существующие message_id.")
                elif positions[start] > positions[end]:
                    error(cid, "REVERSED_CHECKED_RANGE", "from не может быть позже to в порядке транскрипта.")
                else:
                    range_valid = True
        requires_range = answer == "no" or (scale_value is not None and scale_max is not None and scale_value < scale_max)
        if requires_range and not range_valid:
            error(cid, "CHECKED_RANGE_REQUIRED", "Ответ no или ступень ниже максимума требует корректного checked_range.")

        evidence = row.get("evidence")
        evidence_count = 0
        if not isinstance(evidence, list):
            error(cid, "EVIDENCE_NOT_LIST", "evidence должен быть массивом, в том числе пустым при пропущенном действии.")
        else:
            evidence_count = len(evidence)
            for item in evidence:
                if not isinstance(item, dict):
                    error(cid, "INVALID_EVIDENCE", "Каждый evidence должен быть объектом с message_id и quote.")
                    continue
                mid, quote = item.get("message_id"), item.get("quote")
                if not isinstance(mid, str) or mid not in messages:
                    error(cid, "UNKNOWN_EVIDENCE_MESSAGE", "message_id доказательства отсутствует в транскрипте.")
                if not isinstance(quote, str) or not quote.strip():
                    error(cid, "QUOTE_REQUIRED", "Цитата quote должна быть непустой строкой.")
                else:
                    if len(quote) > 200:
                        error(cid, "QUOTE_TOO_LONG", "Цитата должна содержать не более 200 символов.")
                    if isinstance(mid, str) and mid in messages and normalize_text(quote) not in normalize_text(messages[mid]["text"]):
                        error(cid, "QUOTE_NOT_IN_MESSAGE", "Цитата не содержится целиком в указанной реплике; скопируйте её дословно.")
        omitted = answer == "no" or scale_value == Decimal(0)
        if evidence_count == 0:
            if omitted:
                if not range_valid and not requires_range:
                    error(cid, "CHECKED_RANGE_REQUIRED", "Пропущенное действие без evidence требует checked_range.")
            else:
                error(cid, "EVIDENCE_REQUIRED", "Найденное действие или N/A требует хотя бы одного evidence с цитатой.")
    return errors


def enrich_criteria(payload: dict, conversation: dict) -> list[dict]:
    """Copy results and annotate evidence with its source role; validate first."""
    messages = {message["message_id"]: message for message in conversation["transcript"]}
    enriched = deepcopy(payload["criteria"])
    for row in enriched:
        for evidence in row.get("evidence", []):
            evidence["speaker"] = messages[evidence["message_id"]]["speaker"]
    return enriched
