"""Pure, config-driven score calculation; no database or model dependencies.

Normalized scores/points and displayed earned values are rounded (ROUND_HALF_UP,
two decimal places); configured weights and applicable weight sums remain exact.
Totals and section scores use the unrounded earned values. Inactive criteria are
absent from the result and must not have answers. Unscored and N/A criteria have
earned=None, effective_weight=0.00, points=0.00. If no applicable positive weight
exists, total and section scores are None, as are applicable criteria's normalized
weights and points. No flag changes the numerical score.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any


ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


class ScoringError(ValueError):
    """The checklist or answers cannot be scored without inventing values."""


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ScoringError(f"{label} must be a finite non-negative number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ScoringError(f"{label} must be a finite non-negative number") from None
    if not result.is_finite() or result < ZERO:
        raise ScoringError(f"{label} must be a finite non-negative number")
    return result


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_scores(checklist: dict, answers: dict[str, str]) -> dict:
    """Calculate scores from a checklist configuration and all its active answers.

    Each answer is a string: ``yes``, ``no``, ``na``, or an exact key from the
    configured numerical scale. Raises ScoringError on missing/extra answers,
    forbidden N/A, invalid scale steps, or an invalid arithmetic configuration.
    Return values remain Decimal for callers to serialize explicitly at API edges.
    """
    if not isinstance(answers, dict) or not isinstance(checklist.get("criteria"), list):
        raise ScoringError("Expected a checklist criteria list and an answers mapping")
    all_criteria = checklist["criteria"]
    ids = [criterion.get("criterion_id") for criterion in all_criteria]
    if any(not isinstance(cid, str) or not cid for cid in ids):
        raise ScoringError("Each criterion must have a non-empty criterion_id")
    if len(set(ids)) != len(ids):
        raise ScoringError("Duplicate criterion_id")
    criteria = [criterion for criterion in all_criteria if criterion.get("active", True)]
    expected_ids = {criterion["criterion_id"] for criterion in criteria}
    if set(answers) != expected_ids:
        missing = sorted(expected_ids - set(answers))
        extra = sorted(str(cid) for cid in set(answers) - expected_ids)
        raise ScoringError(f"Answers do not cover active criteria: missing={missing}, extra={extra}")

    with localcontext() as context:
        context.prec = 50
        rows: list[dict] = []
        total_earned = ZERO
        applicable_weight = ZERO
        gross_violation = False
        section_totals: dict[str, dict] = {}
        for section in checklist.get("sections", []):
            section_totals[section["id"]] = {"earned": ZERO, "applicable_weight": ZERO}

        for criterion in criteria:
            cid = criterion["criterion_id"]
            answer = answers[cid]
            kind = criterion.get("type")
            if kind not in {"yes_no", "scale", "flag"}:
                raise ScoringError(f"Unsupported criterion type for {cid}")
            weight = _decimal(criterion.get("weight", 0), f"Weight of {cid}")
            scored = criterion.get("scored", True)
            if kind == "flag" and (weight != ZERO or scored):
                raise ScoringError(f"Flag {cid} must have weight=0 and scored=false")

            scale_values = {}
            if kind == "scale":
                scale = criterion.get("scale")
                if not isinstance(scale, dict) or len(scale) < 2:
                    raise ScoringError(f"Scale {cid} must have at least two steps")
                if any(not isinstance(step, str) for step in scale):
                    raise ScoringError(f"Scale keys for {cid} must be strings")
                scale_values = {step: _decimal(step, f"Scale step of {cid}") for step in scale}
                if len(set(scale_values.values())) != len(scale_values):
                    raise ScoringError(f"Scale {cid} contains duplicate numerical steps")
                if max(scale_values.values()) == ZERO:
                    raise ScoringError(f"Scale {cid} must have a positive maximum")

            if not isinstance(answer, str):
                raise ScoringError(f"Answer for {cid} must be a string")
            if answer == "na":
                if kind == "flag" or not criterion.get("allow_na", False):
                    raise ScoringError(f"N/A is not allowed for {cid}")
            elif kind in {"yes_no", "flag"}:
                if answer not in {"yes", "no"}:
                    raise ScoringError(f"Unsupported answer for {cid}: {answer}")
            elif answer not in scale_values:
                raise ScoringError(f"Answer for {cid} is not a configured scale step: {answer}")

            applicable = bool(scored and answer != "na")
            earned = None
            if applicable:
                if kind == "yes_no":
                    earned = weight if answer == "yes" else ZERO
                elif kind == "scale":
                    earned = weight * scale_values[answer] / max(scale_values.values())
                total_earned += earned
                applicable_weight += weight

            section_id = criterion.get("section")
            if section_id is not None:
                section = section_totals.setdefault(section_id, {"earned": ZERO, "applicable_weight": ZERO})
                if applicable:
                    section["earned"] += earned
                    section["applicable_weight"] += weight
            if kind == "flag" and answer == "yes":
                gross_violation = True

            rows.append({
                "criterion_id": cid,
                "answer": answer,
                "earned": earned,
                "weight": weight,
                "applicable": applicable,
            })

        for row in rows:
            applicable = row.pop("applicable")
            if not applicable:
                row["effective_weight"] = _round(ZERO)
                row["points"] = _round(ZERO)
            elif applicable_weight == ZERO:
                row["effective_weight"] = None
                row["points"] = None
            else:
                row["effective_weight"] = _round(row["weight"] / applicable_weight * HUNDRED)
                row["points"] = _round(row["earned"] / applicable_weight * HUNDRED)
            if row["earned"] is not None:
                row["earned"] = _round(row["earned"])

        sections = []
        for section_id, values in section_totals.items():
            section_weight = values["applicable_weight"]
            sections.append({
                "section_id": section_id,
                "section_score": (
                    _round(values["earned"] / section_weight * HUNDRED) if section_weight else None
                ),
                "earned": _round(values["earned"]),
                "applicable_weight": section_weight,
            })

        return {
            "total": _round(total_earned / applicable_weight * HUNDRED) if applicable_weight else None,
            "gross_violation": gross_violation,
            "criteria": rows,
            "sections": sections,
        }
