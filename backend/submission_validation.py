"""Read-only submission audit with arithmetic independent of the runtime scorer."""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


# Deliberately independent of backend.scoring and the exporter's validation.
CRITERIA = {
    "contact": (10, False, None),
    "discovery": (15, True, None),
    "identification": (20, True, None),
    "consultation": (20, True, (0, 5, 10, 15, 20)),
    "empathy": (15, False, None),
    "dialogue": (15, False, (0, 4, 7, 10)),
    "closure": (5, True, None),
    "critical": (0, False, None),
}


def _issue(issues, code, path, message):
    issues.append({"code": code, "path": path, "message": message})


class _JSONObject(dict):
    def __init__(self, pairs):
        super().__init__(pairs)
        counts = Counter(key for key, _ in pairs)
        self.duplicates = [key for key, count in counts.items() if count > 1]


def read_submission_file(path):
    """Decode without repairs; report duplicate keys instead of silently accepting them."""
    issues = []
    try:
        content = Path(path).read_bytes().decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        _issue(issues, "INVALID_UTF8", "$", "Файл не является UTF-8.")
        return None, issues
    except OSError:
        _issue(issues, "FILE_UNAVAILABLE", "$", "Файл не найден или недоступен для чтения.")
        return None, issues
    try:
        def reject_constant(value):
            raise ValueError("Non-finite JSON number")
        payload = json.loads(content, object_pairs_hook=_JSONObject, parse_float=Decimal,
                             parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        _issue(issues, "INVALID_JSON", "$", f"Некорректный JSON: строка {exc.lineno}, столбец {exc.colno}.")
        return None, issues
    except (ValueError, OverflowError, RecursionError):
        _issue(issues, "INVALID_JSON", "$", "Некорректный JSON или недопустимое числовое значение.")
        return None, issues
    def inspect(value, location):
        if isinstance(value, _JSONObject):
            for key in value.duplicates:
                _issue(issues, "DUPLICATE_JSON_KEY", location, f"Ключ {key!r} повторяется в объекте.")
            for key, child in value.items():
                inspect(child, location + "." + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, f"{location}[{index}]")
    inspect(payload, "$")
    return payload, issues


def independent_score(values):
    """Compute a Decimal score from official raw values, never importing scoring.py."""
    earned = Decimal(0)
    applicable = Decimal(0)
    for code, (weight, nullable, scale) in CRITERIA.items():
        if code not in values:
            raise ValueError("Missing criterion")
        value = values[code]
        if value is None:
            if not nullable:
                raise ValueError("N/A forbidden")
            continue
        if scale is None:
            if type(value) is not bool:
                raise ValueError("Boolean required")
        elif type(value) is not int or value not in scale:
            raise ValueError("Permitted integer scale value required")
        if code == "critical":
            continue
        applicable += Decimal(weight)
        earned += Decimal(weight) * (Decimal(value) / Decimal(max(scale)) if scale else Decimal(int(value)))
    if not applicable:
        return None
    return (earned / applicable * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fields(value, required, path, issues):
    if not isinstance(value, dict):
        _issue(issues, "OBJECT_REQUIRED", path, "Ожидается JSON-объект.")
        return False
    missing = required - set(value)
    extra = set(value) - required
    if missing:
        _issue(issues, "MISSING_FIELDS", path, "Отсутствуют поля: " + ", ".join(sorted(missing)))
    if extra:
        _issue(issues, "EXTRA_FIELDS", path, "Лишние поля: " + ", ".join(sorted(map(str, extra))))
    return True


def _number(value):
    if type(value) not in {int, float, Decimal}:
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def inspect_submission(payload, *, split, records=None, expected_models=None,
                       expected_mode="external_api"):
    """Return all observable violations; unknown reference checks are handled by the CLI."""
    issues = []
    if split not in {"dev", "prod"}:
        _issue(issues, "INVALID_SPLIT", "$", "Укажите dev или prod.")
        return issues
    if not _fields(payload, {"schema_version", "team_name", "model", "results"}, "$", issues):
        return issues
    if payload.get("schema_version") != "1.0":
        _issue(issues, "SCHEMA_VERSION", "$.schema_version", "Требуется строка 1.0.")
    if not isinstance(payload.get("team_name"), str) or not payload["team_name"].strip():
        _issue(issues, "TEAM_NAME", "$.team_name", "Название команды должно быть непустой строкой.")
    model = payload.get("model")
    if _fields(model, {"mode", "name"}, "$.model", issues):
        mode, name = model.get("mode"), model.get("name")
        if mode not in ("local", "external_api", "hybrid"):
            _issue(issues, "MODEL_MODE", "$.model.mode", "Недопустимый режим модели.")
        elif expected_mode is not None and mode != expected_mode:
            _issue(issues, "MODEL_MODE_MISMATCH", "$.model.mode", "Для текущего Meta API требуется external_api.")
        if not isinstance(name, str) or not name.strip():
            _issue(issues, "MODEL_NAME", "$.model.name", "Имя модели должно быть непустой строкой.")
        elif expected_models is not None:
            names = [part.strip() for part in name.split(",")]
            if not all(names) or len(names) != len(set(names)) or set(names) != set(expected_models):
                _issue(issues, "MODEL_NAME_MISMATCH", "$.model.name", "Имя модели не совпадает с журналом вызовов выбранных оценок.")
    results = payload.get("results")
    if not isinstance(results, list):
        _issue(issues, "RESULTS_ARRAY", "$.results", "results должен быть массивом.")
        return issues
    expected_count = 65 if split == "dev" else 10
    if len(results) != expected_count:
        _issue(issues, "RESULT_COUNT", "$.results", f"Требуется {expected_count} записей, получено {len(results)}.")
    reference = {}
    if records is not None:
        for record in records:
            cid = record.get("conversation_id")
            if cid in reference:
                _issue(issues, "DATASET_DUPLICATE_ID", "dataset", "Исходный набор содержит повтор ID.")
            reference[cid] = record
        channels = dict(Counter(r.get("channel") for r in records))
        expected_channels = {"agent chat": 60, "voice call": 5} if split == "dev" else {"agent chat": 10}
        if len(records) != expected_count or channels != expected_channels:
            _issue(issues, "DATASET_COMPOSITION", "dataset", "Неверный состав: DEV — 60 чатов и 5 звонков; PROD — 10 чатов.")
        for cid, record in reference.items():
            transcript = record.get("transcript")
            normalized = record.get("normalized_transcript")
            if not isinstance(transcript, list):
                _issue(issues, "DATASET_TRANSCRIPT", f"dataset.{cid}", "Исходный transcript не является массивом.")
                continue
            if normalized is not None:
                if len(normalized) != len(transcript):
                    _issue(issues, "MESSAGE_NUMBERING", f"dataset.{cid}", "Число нормализованных реплик не совпадает с исходным массивом.")
                for index, item in enumerate(normalized, 1):
                    source = transcript[index - 1] if index <= len(transcript) else {}
                    if item.get("message_id") != f"m{index}" or any(item.get(k) != source.get(k) for k in ("speaker", "text")):
                        _issue(issues, "MESSAGE_NUMBERING", f"dataset.{cid}.m{index}", "message_id или содержание не совпадает с исходной позицией transcript.")
    seen = set()
    for index, result in enumerate(results):
        location = f"$.results[{index}]"
        if not isinstance(result, dict):
            _issue(issues, "OBJECT_REQUIRED", location, "Запись результата должна быть объектом.")
            continue
        cid = result.get("conversation_id")
        if not isinstance(cid, str) or not cid:
            _issue(issues, "CONVERSATION_ID", location + ".conversation_id", "Требуется непустой строковый ID.")
            source = None
        else:
            if cid in seen:
                _issue(issues, "DUPLICATE_CONVERSATION_ID", location, "ID повторяется в results.")
            seen.add(cid)
            source = reference.get(cid)
            if records is not None and source is None:
                _issue(issues, "UNKNOWN_CONVERSATION_ID", location, "ID отсутствует в исходном наборе.")
        status = result.get("status")
        fields = {"conversation_id", "status", "criteria", "score"}
        _fields(result, fields | ({"error"} if status == "error" else set()), location, issues)
        if status == "error":
            if result.get("criteria") != []:
                _issue(issues, "ERROR_CRITERIA", location + ".criteria", "Для error требуется пустой массив.")
            if result.get("score") is not None:
                _issue(issues, "ERROR_SCORE", location + ".score", "Для error требуется null, не нулевая оценка.")
            if not isinstance(result.get("error"), str) or not result["error"].strip():
                _issue(issues, "ERROR_REASON", location + ".error", "Причина ошибки должна быть непустой строкой.")
            continue
        if status != "ok":
            _issue(issues, "INVALID_STATUS", location + ".status", "Допустимы только ok или error.")
            continue
        transcript = source.get("transcript") if source else None
        if isinstance(transcript, list) and not any(isinstance(m, dict) and m.get("speaker") == "operator" for m in transcript):
            _issue(issues, "NO_OPERATOR_MESSAGES", location, "Без реплик оператора успешная числовая оценка недопустима; нужен status=error.")
        criteria = result.get("criteria")
        if not isinstance(criteria, list):
            _issue(issues, "CRITERIA_ARRAY", location + ".criteria", "criteria должен быть массивом.")
            continue
        criterion_ids, values, bad_value = [], {}, False
        for cindex, criterion in enumerate(criteria):
            cloc = f"{location}.criteria[{cindex}]"
            if not _fields(criterion, {"criterion_id", "value", "rationale", "evidence_turns"}, cloc, issues):
                bad_value = True
                continue
            code = criterion.get("criterion_id")
            if isinstance(code, str):
                criterion_ids.append(code)
            if not isinstance(code, str) or code not in CRITERIA:
                _issue(issues, "UNKNOWN_CRITERION", cloc + ".criterion_id", "Неизвестный код критерия.")
                bad_value = True
            else:
                _, nullable, scale = CRITERIA[code]
                value = criterion.get("value")
                valid = value is None and nullable
                if value is not None:
                    valid = type(value) is bool if scale is None else type(value) is int and value in scale
                if not valid or "value" not in criterion:
                    _issue(issues, "CRITERION_VALUE", cloc + ".value", "Недопустимый тип, ступень шкалы или N/A.")
                    bad_value = True
                values[code] = value
            rationale = criterion.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                _issue(issues, "RATIONALE", cloc + ".rationale", "Требуется непустое объяснение.")
            turns = criterion.get("evidence_turns")
            if not isinstance(turns, list):
                _issue(issues, "EVIDENCE_ARRAY", cloc + ".evidence_turns", "Требуется массив номеров реплик.")
            else:
                valid_turns = [n for n in turns if type(n) is int and n >= 1]
                if len(valid_turns) != len(turns):
                    _issue(issues, "EVIDENCE_INTEGER", cloc + ".evidence_turns", "Номера должны быть целыми числами от 1; bool недопустим.")
                if len(set(valid_turns)) != len(valid_turns):
                    _issue(issues, "EVIDENCE_DUPLICATE", cloc + ".evidence_turns", "Номера реплик повторяются.")
                if isinstance(transcript, list) and any(n > len(transcript) for n in valid_turns):
                    _issue(issues, "EVIDENCE_OUT_OF_RANGE", cloc + ".evidence_turns", "Номер выходит за пределы полного исходного transcript.")
                if (criterion.get("value") is False or criterion.get("value") is None) and not turns:
                    _issue(issues, "EVIDENCE_REQUIRED", cloc + ".evidence_turns", "Для Нет и N/A нужны места ожидания действия или обоснования N/A.")
        counts = Counter(criterion_ids)
        missing = set(CRITERIA) - set(criterion_ids)
        if len(criteria) != 8 or missing or any(count != 1 for count in counts.values()):
            _issue(issues, "CRITERION_COVERAGE", location + ".criteria", "Нужны ровно восемь официальных критериев по одному экземпляру.")
        score = result.get("score")
        if not _fields(score, {"final_score", "critical_flag"}, location + ".score", issues):
            continue
        number = _number(score.get("final_score"))
        if number is None or not 0 <= number <= 100 or number != number.quantize(Decimal("0.01")):
            _issue(issues, "FINAL_SCORE_VALUE", location + ".score.final_score", "Требуется конечное число 0–100 с округлением до двух знаков.")
        if type(score.get("critical_flag")) is not bool or ("critical" in values and score.get("critical_flag") is not values["critical"]):
            _issue(issues, "CRITICAL_FLAG", location + ".score.critical_flag", "Флаг должен быть boolean и совпадать с critical.")
        if not bad_value and set(values) == set(CRITERIA) and len(criteria) == 8 and all(count == 1 for count in counts.values()):
            expected = independent_score(values)
            if expected is None:
                _issue(issues, "NO_APPLICABLE_CRITERIA", location + ".score", "Нет применимых критериев: нужен error и score=null.")
            elif number is not None and number != expected:
                _issue(issues, "FINAL_SCORE_MISMATCH", location + ".score.final_score", f"Независимый пересчёт: {expected:.2f}.")
    if records is not None:
        missing = set(reference) - seen
        if missing:
            _issue(issues, "MISSING_CONVERSATIONS", "$.results", "Отсутствуют ID: " + ", ".join(sorted(map(str, missing))))
    return issues
