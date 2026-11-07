"""Cost summaries use persisted usage/prices, including failures and appeal calls."""
import json
from decimal import Decimal, ROUND_HALF_UP

from backend.llm_client import summarize_calls


def _summary(rows):
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    costs = [Decimal(r["cost"]) for r in rows if r["cost"] is not None]
    configured = bool(rows) and all(r["price_input_per_1m"] is not None and r["price_output_per_1m"] is not None for r in rows)
    complete = configured and len(costs) == len(rows)
    return {
        "calls": len(rows), "successful_calls": sum(r["error_code"] is None for r in rows),
        "failed_calls": sum(r["error_code"] is not None for r in rows),
        "retry_calls": sum(r["purpose"] == "retry" for r in rows),
        **{key: sum(r[key] for r in rows if r[key] is not None) if any(r[key] is not None for r in rows) else (0 if not rows else None) for key in keys},
        "usage_complete": all(all(r[key] is not None for key in keys) for r in rows),
        "cost": str(sum(costs, Decimal(0))) if complete else None,
        "known_cost": str(sum(costs, Decimal(0))) if costs else None,
        "cost_complete": complete,
        "price_status": "configured" if configured else "price_not_set",
        "price_message": "цена задана" if configured else "цена не задана",
    }


def get_cost_stats(conn):
    calls = [dict(r) for r in conn.execute("SELECT * FROM llm_calls")]
    total = _summary(calls)
    evaluations = []
    for row in conn.execute("SELECT evaluation_id,conversation_id,checklist_version_id,status,job_id,"
                            "final_result_json,original_result_json FROM evaluations ORDER BY created_at"):
        item = dict(row)
        final_json, original_json = item.pop("final_result_json"), item.pop("original_result_json")
        final = json.loads(final_json or original_json or "{}")
        item["usage"] = summarize_calls(conn, item["evaluation_id"])
        if item["usage"]["calls"] == 0 and final.get("reevaluation", {}).get("mode") == "recalculation":
            # No model call was needed: zero cost is known even without prices.
            # Keep price_status unchanged instead of inventing a token price.
            item["usage"].update(prompt_tokens=0, completion_tokens=0, total_tokens=0,
                                 usage_complete=True, cost="0", known_cost="0", cost_complete=True)
        item["usage"]["price_message"] = "цена задана" if item["usage"]["price_status"] == "configured" else "цена не задана"
        evaluations.append(item)
    linked = [r for r in calls if r["evaluation_id"] is not None]
    linked_summary = _summary(linked)
    count = len(evaluations)
    def mean(value, *, money=False):
        if not count or value is None:
            return None
        value = Decimal(str(value)) / count
        # Tiny per-call charges must not disappear when averaged. Money keeps
        # Decimal division precision; only mean token counts round to 2 places.
        return format(value, "f") if money else str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    average = {key: mean(linked_summary[key]) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    average["usage_complete"] = bool(evaluations) and all(item["usage"]["usage_complete"] for item in evaluations)
    average["cost_complete"] = bool(evaluations) and all(item["usage"]["cost_complete"] for item in evaluations)
    average["cost"] = (
        mean(sum((Decimal(item["usage"]["cost"]) for item in evaluations), Decimal(0)), money=True)
        if average["cost_complete"] else None
    )
    if evaluations and not calls and average["cost_complete"]:
        total.update(cost="0", known_cost="0", cost_complete=True)
    return {"evaluation_count": count, "evaluations": evaluations, "total": total,
            "average_per_evaluation": average, "unassigned_calls": sum(r["evaluation_id"] is None for r in calls),
            "average_basis": "Все сохранённые оценки, включая failed; токены — известная сумма, включая апелляции и повторы."}
