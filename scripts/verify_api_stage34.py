"""Real API acceptance capture. This script makes billable model calls (no mocks)."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.config import load_data_settings
from backend.main import create_app

IDS = ["p1-ru-003", "p1-ru-001", "p1-ru-002", "p1-kz-001", "p1-kz-002", "p1-en-001", "p1-en-002", "p4-ru-001"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["baseline", "appeal", "partial", "capture"])
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--final-answer")
    parser.add_argument("--reason")
    parser.add_argument("--comment", help="Appeal argument supplied locally; never embed organizer transcript data in code.")
    args = parser.parse_args()
    if args.phase == "appeal" and any(not value or not value.strip() for value in (args.final_answer, args.reason, args.comment)):
        parser.error("appeal requires --final-answer, --reason and --comment (explicit reviewer input)")
    folder = Path(args.report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    def save(name, value):
        with (folder / f"{name}.json").open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)

    def checkpoint():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def call(client, name, method, path, body=None, expect=(200, 201, 202)):
        started = time.monotonic()
        response = client.request(method, path, json=body) if body is not None else client.request(method, path)
        body_json = response.json()
        save(name, {"request": {"method": method, "path": path, "body": body},
                    "status_code": response.status_code, "elapsed_seconds": round(time.monotonic() - started, 3), "body": body_json})
        if response.status_code not in expect:
            print(json.dumps({"step": name, "http_status": response.status_code, "error": body_json.get("error")}, ensure_ascii=False), flush=True)
            raise RuntimeError(f"Acceptance failed at {name}; response saved.")
        return body_json

    def batch(client, prefix, body):
        started = time.monotonic()
        job = call(client, prefix + "_submit", "POST", "/api/evaluations/batch", body)
        history, last = [], None
        while True:
            response = client.get("/api/jobs/" + job["job_id"])
            response.raise_for_status()
            status = response.json()
            signature = (status["status"], status["done"], len(status["errors"]))
            if signature != last:
                event = {"elapsed_seconds": round(time.monotonic() - started, 3), "status": status["status"],
                         "done": status["done"], "total": status["total"], "errors": status["errors"]}
                history.append(event)
                print(json.dumps({"step": prefix, **event}, ensure_ascii=False), flush=True)
                last = signature
            if status["status"] in {"completed", "failed", "interrupted"}:
                break
            if time.monotonic() - started > 1800:
                raise TimeoutError("Batch exceeded 30 minutes; persisted job can be inspected separately.")
            time.sleep(.5)
        save(prefix + "_progress", history)
        final = call(client, prefix + "_job", "GET", "/api/jobs/" + job["job_id"])
        results = []
        for item in final["items"]:
            if item["evaluation_id"]:
                results.append(call(client, prefix + "_" + item["conversation_id"], "GET", "/api/evaluations/" + item["evaluation_id"]))
        skipped_ids = {item["evaluation_id"] for item in final["items"] if item["status"] == "skipped"}
        new_results = [result for result in results if result["evaluation_id"] not in skipped_ids]
        summary = {"job_id": job["job_id"], "elapsed_seconds": round(time.monotonic() - started, 3),
                   "status": final["status"], "done": final["done"], "total": final["total"], "errors": final["errors"],
                   "prompt_tokens": sum(r["usage"]["prompt_tokens"] or 0 for r in new_results),
                   "completion_tokens": sum(r["usage"]["completion_tokens"] or 0 for r in new_results),
                   "total_tokens": sum(r["usage"]["total_tokens"] or 0 for r in new_results),
                   "calls": sum(r["usage"]["calls"] for r in new_results),
                   "retry_calls": sum(c["purpose"] == "retry" for r in new_results for c in r["llm_calls"]),
                   "evaluations": [{"conversation_id": r["conversation_id"], "evaluation_id": r["evaluation_id"],
                                    "status": r["status"], "total": r["total"], "usage": r["usage"],
                                    "reevaluation": r.get("reevaluation")} for r in results]}
        save(prefix + "_summary", summary)
        print(json.dumps({k: v for k, v in summary.items() if k != "evaluations"}, ensure_ascii=False), flush=True)
        return summary

    with TestClient(create_app()) as client:
        if args.phase == "baseline":
            call(client, "health", "GET", "/api/health")
            config = call(client, "checklist_baseline", "GET", "/api/checklists/active")
            state["baseline_version_id"] = config["checklist_version_id"]
            state["dataset_url_configured"] = bool(load_data_settings().api_url)
            if state["dataset_url_configured"]:
                call(client, "dataset_load", "POST", "/api/dataset/load", {"group_id": "DEV"})
            else:
                call(client, "dataset_unconfigured", "POST", "/api/dataset/load", {"group_id": "DEV"}, expect=(503,))
            single = call(client, "single_evaluation", "POST", "/api/evaluations", {"conversation_id": "p1-ru-003", "checklist_version_id": state["baseline_version_id"], "output_language": "ru"})
            state["single_evaluation_id"] = single["evaluation_id"]
            checkpoint()
            print(json.dumps({"step": "single", "evaluation_id": single["evaluation_id"], "status": single["status"], "total": single["total"], "usage": single["usage"]}, ensure_ascii=False), flush=True)
            state["full_batch"] = batch(client, "full", {"conversation_ids": IDS, "checklist_version_id": state["baseline_version_id"], "force": True})
            checkpoint()
        elif args.phase == "appeal":
            appeal = call(client, "appeal_open", "POST", "/api/appeals", {
                "evaluation_id": state["single_evaluation_id"], "criterion_id": "06",
                "comment": args.comment})
            state["appeal_id"] = appeal["appeal_id"]
            checkpoint()
            advice = call(client, "appeal_suggestion", "POST", f"/api/appeals/{appeal['appeal_id']}/ai-suggest", {"output_language": "ru"})
            print(json.dumps({"step": "suggestion", "result": advice}, ensure_ascii=False), flush=True)
            decision = call(client, "appeal_decision", "POST", f"/api/appeals/{appeal['appeal_id']}/decision", {
                "decision": "change", "final_answer": args.final_answer, "reason": args.reason})
            state["appeal_decision"] = decision
            call(client, "appeal_detail", "GET", f"/api/appeals/{appeal['appeal_id']}")
            call(client, "single_after_appeal", "GET", "/api/evaluations/" + state["single_evaluation_id"])
            call(client, "decision_conflict", "POST", f"/api/appeals/{appeal['appeal_id']}/decision", {"decision": "change", "final_answer": args.final_answer, "reason": args.reason}, expect=(409,))
            checkpoint()
            print(json.dumps({"step": "decision", "result": decision}, ensure_ascii=False), flush=True)
        elif args.phase == "partial":
            saved = json.loads((folder / "checklist_baseline.json").read_text(encoding="utf-8"))["body"]
            new = {key: copy.deepcopy(saved[key]) for key in ("name", "sections", "criteria", "checklist_id")}
            target = next(c for c in new["criteria"] if c["criterion_id"] == "05")
            target["description"] = "Если клиент явно описал неудобство или недовольство, оператор должен отреагировать именно на эту проблему. Одна формальная вежливость без такой реакции недостаточна; при отсутствии неудобства достаточно вежливого общения."
            version = call(client, "checklist_new", "POST", "/api/checklists", new)
            state["partial_version_id"] = version["checklist_version_id"]
            checkpoint()
            state["partial_batch"] = batch(client, "partial", {"conversation_ids": IDS, "checklist_version_id": version["checklist_version_id"], "criterion_ids": ["05"], "force": True})
            state["token_reduction_percent"] = round((1 - state["partial_batch"]["total_tokens"] / state["full_batch"]["total_tokens"]) * 100, 2)
            checkpoint()
        else:
            call(client, "conversations_filtered", "GET", "/api/conversations?language=EN&channel=agent%20chat&status=completed&q=p1-en-001")
            call(client, "conversation_detail", "GET", "/api/conversations/p1-ru-003")
            call(client, "history", "GET", "/api/conversations/p1-ru-003/evaluations")
            call(client, "checklist_versions", "GET", "/api/checklists")
            call(client, "checklist_activation", "POST", "/api/checklists/" + state["partial_version_id"] + "/activate")
            call(client, "appeals_reviewed", "GET", "/api/appeals?status=reviewed")
            call(client, "cost_stats", "GET", "/api/stats/cost")
            call(client, "not_found", "GET", "/api/conversations/acceptance-nonexistent", expect=(404,))
            call(client, "invalid_request", "POST", "/api/evaluations", {}, expect=(422,))
            state["idempotent_batch"] = batch(client, "skip", {"conversation_ids": IDS, "checklist_version_id": state["partial_version_id"]})
            checkpoint()
    if args.phase == "capture":
        # Exercise uploads against a separate real SQLite/API instance so the
        # organizer's DEV set stays intact. No model is called for these fixtures.
        with TestClient(create_app(folder / "upload_check.sqlite3")) as upload_client:
            call(upload_client, "upload_json", "POST", "/api/conversations/upload", {
                "conversation_id": "api-upload-example", "language": "RU", "channel": "agent chat",
                "transcript": "client: Какие тарифы?\noperator: Здравствуйте, меня зовут Анна."})
            call(upload_client, "upload_unchanged", "POST", "/api/conversations/upload", {
                "conversation_id": "api-upload-example", "language": "RU", "channel": "agent chat",
                "transcript": "client: Какие тарифы?\noperator: Здравствуйте, меня зовут Анна."})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
