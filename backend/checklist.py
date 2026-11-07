"""Config-driven checklists with append-only SQLite versions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

SEED_PATH = Path(__file__).resolve().parent / "configs" / "default_checklist.json"
ACTIVE_KEY = "active_checklist_version_id"
_COLUMNS = "checklist_version_id, checklist_id, version, name, config_json, weight_sum, created_at"


class ChecklistValidationError(ValueError):
    """The requested checklist configuration is not valid."""


class ChecklistNotFoundError(LookupError):
    """The requested or active checklist version does not exist."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChecklistValidationError(f"{field}: требуется непустая строка.")
    return value.strip()


def _number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ChecklistValidationError(f"{field}: требуется число.")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ChecklistValidationError(f"{field}: требуется число.") from None
    if not number.is_finite() or number < 0:
        raise ChecklistValidationError(f"{field}: требуется конечное число >= 0.")
    return number


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ChecklistValidationError(f"{field}: требуется true или false.")
    return value


def validate_checklist(config: dict[str, Any]) -> dict[str, Any]:
    """Return an independent normalized config; metadata is assigned at save time.

    Integer weights remain JSON numbers. Fractional weights are decimal strings
    to preserve their precision through JSON/SQLite round trips.
    """
    if not isinstance(config, dict):
        raise ChecklistValidationError("Чек-лист должен быть JSON-объектом.")
    name = _text(config.get("name"), "name")
    raw_sections = config.get("sections", [])
    raw_criteria = config.get("criteria")
    if not isinstance(raw_sections, list):
        raise ChecklistValidationError("sections должен быть массивом.")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ChecklistValidationError("criteria должен содержать хотя бы один критерий.")

    sections = []
    section_ids = set()
    for section in raw_sections:
        if not isinstance(section, dict):
            raise ChecklistValidationError("Каждая секция должна быть объектом.")
        section_id = _text(section.get("id"), "section.id")
        if section_id in section_ids:
            raise ChecklistValidationError(f"Повтор section.id: {section_id}.")
        section_ids.add(section_id)
        sections.append({"id": section_id, "name": _text(section.get("name"), "section.name")})

    criteria = []
    criterion_ids = set()
    for index, item in enumerate(raw_criteria, start=1):
        if not isinstance(item, dict):
            raise ChecklistValidationError("Каждый критерий должен быть объектом.")
        criterion_id = _text(item.get("criterion_id"), "criterion_id")
        if criterion_id in criterion_ids:
            raise ChecklistValidationError(f"Повтор criterion_id: {criterion_id}.")
        criterion_ids.add(criterion_id)
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in {"yes_no", "scale", "flag"}:
            raise ChecklistValidationError(f"{criterion_id}: неизвестный type.")
        section = item.get("section")
        if section is not None and (not isinstance(section, str) or section not in section_ids):
            raise ChecklistValidationError(f"{criterion_id}: section отсутствует в sections.")
        weight = _number(item.get("weight"), f"{criterion_id}.weight")
        allow_na = _bool(item.get("allow_na", False), f"{criterion_id}.allow_na")
        scored = _bool(item.get("scored", kind != "flag"), f"{criterion_id}.scored")
        active = _bool(item.get("active", True), f"{criterion_id}.active")
        na_condition = item.get("na_condition")
        if allow_na:
            na_condition = _text(na_condition, f"{criterion_id}.na_condition")
        elif na_condition is not None and not isinstance(na_condition, str):
            raise ChecklistValidationError(f"{criterion_id}.na_condition: требуется строка или null.")
        if kind == "flag" and (weight != 0 or scored or allow_na):
            raise ChecklistValidationError(f"{criterion_id}: flag требует weight=0, scored=false, allow_na=false.")
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ChecklistValidationError(f"{criterion_id}.description: требуется строка.")
        order = item.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ChecklistValidationError(f"{criterion_id}.order: требуется целое число.")
        scale = {}
        yes_text = item.get("yes_text")
        no_text = item.get("no_text")
        if kind == "scale":
            raw_scale = item.get("scale")
            if not isinstance(raw_scale, dict) or len(raw_scale) < 2:
                raise ChecklistValidationError(f"{criterion_id}: scale требует минимум две ступени.")
            numeric_keys = set()
            for key, explanation in raw_scale.items():
                if not isinstance(key, str):
                    raise ChecklistValidationError(f"{criterion_id}.scale: ключи должны быть строками.")
                value = _number(key, f"{criterion_id}.scale")
                if value in numeric_keys:
                    raise ChecklistValidationError(f"{criterion_id}.scale: повтор числовой ступени.")
                numeric_keys.add(value)
                scale[key] = _text(explanation, f"{criterion_id}.scale[{key}]")
            if max(numeric_keys) <= 0:
                raise ChecklistValidationError(f"{criterion_id}.scale: максимум должен быть > 0.")
            if yes_text is not None and not isinstance(yes_text, str):
                raise ChecklistValidationError(f"{criterion_id}.yes_text: требуется строка или null.")
            if no_text is not None and not isinstance(no_text, str):
                raise ChecklistValidationError(f"{criterion_id}.no_text: требуется строка или null.")
        else:
            yes_text = _text(yes_text, f"{criterion_id}.yes_text")
            no_text = _text(no_text, f"{criterion_id}.no_text")
        criteria.append({
            "criterion_id": criterion_id,
            "name": _text(item.get("name"), f"{criterion_id}.name"),
            "description": description,
            "section": section,
            "type": kind,
            "weight": int(weight) if weight == weight.to_integral_value() else str(weight),
            "allow_na": allow_na,
            "na_condition": na_condition,
            "yes_text": yes_text,
            "no_text": no_text,
            "scale": scale,
            "scored": scored,
            "order": order,
            "active": active,
        })
    return {"name": name, "sections": sections, "criteria": criteria}


@contextmanager
def _write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Serialize version allocation; preserve transactions owned by the caller."""
    if conn.in_transaction:
        savepoint = f"checklist_{uuid4().hex}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def _decode(row: Any) -> dict[str, Any]:
    result = json.loads(row[4])
    result.update({
        "checklist_version_id": row[0],
        "checklist_id": row[1],
        "version": row[2],
        "name": row[3],
        "weight_sum": row[5],
        "created_at": row[6],
    })
    return result


def get_checklist(conn: sqlite3.Connection, version_id: str | None = None) -> dict[str, Any]:
    if version_id is None:
        pointer = conn.execute("SELECT value FROM app_settings WHERE key=?", (ACTIVE_KEY,)).fetchone()
        if pointer is None:
            raise ChecklistNotFoundError("Активная версия чек-листа не выбрана.")
        version_id = pointer[0]
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM checklist_versions WHERE checklist_version_id=?", (version_id,)
    ).fetchone()
    if row is None:
        raise ChecklistNotFoundError("Версия чек-листа не найдена.")
    return _decode(row)


def list_checklists(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM checklist_versions ORDER BY checklist_id, version DESC"
    ).fetchall()
    return [_decode(row) for row in rows]


def diff_checklists(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Separate answer-changing edits from edits that only affect deterministic scoring.

    Inactive criteria do not participate. Activating one is therefore an addition;
    deactivation is a removal. Weights, scoring participation, order and section
    placement do not change the meaning of an already recorded answer.
    """
    before = {c["criterion_id"]: c for c in (previous or {}).get("criteria", []) if c.get("active", True)}
    after = {c["criterion_id"]: c for c in current["criteria"] if c.get("active", True)}
    semantic_fields = ("name", "description", "type", "allow_na", "na_condition", "yes_text", "no_text", "scale")
    calculation_fields = ("weight", "scored", "section", "order")
    added, changed, recalculation_only, unchanged = [], [], [], []
    for cid, criterion in after.items():
        if cid not in before:
            added.append(cid)
        elif any(before[cid].get(key) != criterion.get(key) for key in semantic_fields):
            changed.append(cid)
        elif any(before[cid].get(key) != criterion.get(key) for key in calculation_fields):
            recalculation_only.append(cid)
        else:
            unchanged.append(cid)
    required = set(added + changed)
    return {
        "previous_version_id": (previous or {}).get("checklist_version_id"),
        "version_id": current.get("checklist_version_id"),
        "evaluation_required": [cid for cid in after if cid in required],
        "added": added, "changed": changed, "recalculation_only": recalculation_only,
        "removed": [cid for cid in before if cid not in after], "unchanged": unchanged,
    }


def save_checklist(
    conn: sqlite3.Connection, config: dict[str, Any], checklist_id: str = "freedom-broker"
) -> dict[str, Any]:
    """Append a version and activate it atomically; incoming version IDs are ignored."""
    normalized = validate_checklist(config)
    checklist_id = _text(checklist_id, "checklist_id")
    weight_sum = sum(
        (Decimal(str(c["weight"])) for c in normalized["criteria"] if c["active"] and c["scored"]),
        Decimal(0),
    )
    version_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with _write_transaction(conn):
        version = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM checklist_versions WHERE checklist_id=?",
            (checklist_id,),
        ).fetchone()[0]
        normalized.update({"checklist_id": checklist_id, "version": version})
        conn.execute(
            "INSERT INTO checklist_versions "
            "(checklist_version_id,checklist_id,version,name,config_json,weight_sum,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (version_id, checklist_id, version, normalized["name"],
             json.dumps(normalized, ensure_ascii=False, allow_nan=False), str(weight_sum), created_at),
        )
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (ACTIVE_KEY, version_id),
        )
    return get_checklist(conn, version_id)


def activate_checklist(conn: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    with _write_transaction(conn):
        version = get_checklist(conn, version_id)
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ACTIVE_KEY, version_id),
        )
    return version


def seed_checklist(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read the seed only for an empty database; never overwrite user configuration."""
    with _write_transaction(conn):
        existing = conn.execute(
            "SELECT checklist_version_id FROM checklist_versions ORDER BY created_at,version LIMIT 1"
        ).fetchone()
        if existing is not None:
            try:
                return get_checklist(conn)
            except ChecklistNotFoundError:
                return activate_checklist(conn, existing[0])
        with SEED_PATH.open(encoding="utf-8") as source:
            config = json.load(source)
        return save_checklist(conn, config)
