"""Three-run CLI consensus, using model mocks only in this test module."""
import asyncio
import copy
import json
import threading
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from backend import batch_reporting, consensus, llm_client
from backend.checklist import get_checklist, save_checklist
from backend.config import DataSettings
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.meta_client import ModelRequestError
from backend.submission import SubmissionError
from backend.submission_validation import inspect_submission
from scripts import process_control_set


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "unit-test-only-key")
    monkeypatch.setenv("META_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("META_MODEL", "test-model")
    monkeypatch.setenv("MAX_CONCURRENCY", "2")
    monkeypatch.setenv("PRICE_INPUT_PER_1M", "")
    monkeypatch.setenv("PRICE_OUTPUT_PER_1M", "")
    monkeypatch.setattr(consensus, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(batch_reporting, "PROJECT_ROOT", tmp_path)
    path = tmp_path / "main.sqlite3"
    init_db(path)
    records = [{"conversation_id": f"synthetic-{i:02d}", "channel": "agent chat",
                "language": ("RU", "KZ", "EN")[i % 3], "audio_url": None,
                "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Покажите общие условия тестового сервиса."},
                               {"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте, я тестовый консультант. Условия размещены в разделе помощи."},
                               {"timestamp": "00:02", "speaker": "operator", "text": "Всего доброго."}]} for i in range(12)]
    with closing(connect_db(path)) as conn:
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": len(records), "records": records}})
        version = get_checklist(conn)["checklist_version_id"]
    settings = DataSettings(path, "", "DEV")
    monkeypatch.setattr(process_control_set, "load_data_settings", lambda: settings)
    return path, version, records


@pytest.fixture
def mock_model(monkeypatch):
    state = {"calls": 0, "per_id": {}, "active": 0, "peak": 0, "pattern": None, "fail_id": None}
    lock = threading.Lock()
    def transport(system, user, **kwargs):
        decoder = json.JSONDecoder()
        meta = decoder.raw_decode(user.split("Данные диалога:\n", 1)[1])[0]
        checklist = decoder.raw_decode(user.split("Чек-лист JSON (только активные критерии):\n", 1)[1])[0]
        cid = meta["conversation_id"]
        with lock:
            state["calls"] += 1
            state["per_id"][cid] = state["per_id"].get(cid, 0) + 1
            number = state["per_id"][cid]
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(.02)
            if cid == state["fail_id"]:
                raise ModelRequestError("MODEL_ACCESS_DENIED", "Синтетическая ошибка доступа.", status_code=401)
            rows = []
            for c in checklist["criteria"]:
                answer = "no" if c["type"] == "flag" else max(c["scale"], key=Decimal) if c["type"] == "scale" else "yes"
                if state["pattern"] and c["criterion_id"] == "03":
                    answer = state["pattern"][(number - 1) % 3]
                reason = "Проверено по синтетическому транскрипту."
                if answer == "na":
                    reason = c["na_condition"] + " Синтетический вопрос является общим."
                rows.append({"criterion_id": c["criterion_id"], "answer": answer, "reasoning": reason,
                    "evidence": [] if answer == "no" else [{"message_id": "m2", "quote": "Здравствуйте, я тестовый консультант."}],
                    "checked_range": {"from": "m1", "to": "m3"} if answer == "no" else None})
            return {"content": json.dumps({"conversation_id": cid, "criteria": rows}, ensure_ascii=False),
                    "model": "test-model", "http_status": 200, "finish_reason": "stop",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        finally:
            with lock:
                state["active"] -= 1
    monkeypatch.setattr(llm_client, "request_completion", transport)
    return state


def run(source, ids=("synthetic-00",), **kwargs):
    path, version, _ = source
    return asyncio.run(consensus.run_consensus_report(path, {"conversation_ids": list(ids), "checklist_version_id": version},
                                                    team_name="Synthetic Team", **kwargs))


def test_majority_reuses_real_evidence_and_identical_repeat_costs_nothing(source, mock_model):
    mock_model["pattern"] = ["yes", "no", "yes"]
    first = run(source)
    assert first["runs"] == 3 and first["usage"]["calls"] == 3
    assert first["usage"]["total_tokens"] == 90
    assert first["counts"] == {"completed": 1, "needs_review": 0, "failed": 0}
    identity = next(c for c in first["submission"]["results"][0]["criteria"] if c["criterion_id"] == "identification")
    assert identity["value"] is True and identity["evidence_turns"] == [2]
    detail = first["consensus"]["synthetic-00"]
    assert len(set(detail["evaluation_ids"])) == 3
    assert detail["criteria"]["03"]["votes"] == {"yes": 2, "no": 1}
    assert detail["criteria"]["03"]["source_evaluation_id"] == detail["evaluation_ids"][0]
    again = run(source)
    assert again["skipped"] == 1 and again["usage"]["calls"] == 0
    assert again["usage"]["cost"] == "0" and again["usage_all_cycles"]["calls"] == 3
    assert again["submission"] == first["submission"] and mock_model["calls"] == 3


def test_three_different_answers_are_error_not_average(source, mock_model):
    mock_model["pattern"] = ["yes", "no", "na"]
    report = run(source)
    row = report["submission"]["results"][0]
    assert row["status"] == "error" and row["score"] is None and row["criteria"] == []
    assert "03" in row["error"] and report["counts"]["needs_review"] == 1
    assert report["usage"]["calls"] == 3


def test_model_failure_does_not_drop_other_dialogue_or_unknown_usage(source, mock_model):
    mock_model["fail_id"] = "synthetic-00"
    report = run(source, ("synthetic-00", "synthetic-01"))
    assert report["counts"] == {"completed": 1, "needs_review": 0, "failed": 1}
    assert report["usage"]["calls"] == 6 and report["usage"]["failed_calls"] == 3
    assert report["usage"]["usage_complete"] is False
    assert report["usage"]["total_tokens"] == 90
    assert report["submission"]["results"][0]["status"] == "error"
    assert report["submission"]["results"][1]["status"] == "ok"
    mock_model["fail_id"] = None
    retry = run(source, ("synthetic-00", "synthetic-01"))
    assert retry["skipped"] == 1 and retry["usage"]["calls"] == 3
    assert retry["usage_all_cycles"]["calls"] == 9
    assert retry["counts"]["completed"] == 2


def test_completed_round_recovers_without_repaying_it(source, mock_model, monkeypatch):
    real = consensus.run_batch_report
    first = True
    async def interrupt_after_recorded_round(*args, **kwargs):
        nonlocal first
        report = await real(*args, **kwargs)
        if first:
            first = False
            raise RuntimeError("Synthetic interruption after a completed round")
        return report
    monkeypatch.setattr(consensus, "run_batch_report", interrupt_after_recorded_round)
    with pytest.raises(RuntimeError):
        run(source)
    assert mock_model["calls"] == 1
    resumed = run(source)
    assert resumed["usage"]["calls"] == 2 and resumed["usage_all_cycles"]["calls"] == 3
    assert mock_model["calls"] == 3


def test_changed_prompt_cannot_reuse_previous_consensus(source, mock_model, monkeypatch, tmp_path):
    first = run(source)
    replacement = tmp_path / "prompt.md"
    replacement.write_bytes(consensus.PROMPT_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(consensus, "PROMPT_PATH", replacement)
    from backend import evaluator
    monkeypatch.setattr(evaluator, "PROMPT_PATH", replacement)
    second = run(source)
    assert second["skipped"] == 0 and second["usage"]["calls"] == 3
    assert first["cache_path"] != second["cache_path"]


def test_changed_user_prompt_invalidates_cache_even_with_same_system_hash(source, mock_model, monkeypatch):
    first = run(source)
    real = consensus.build_prompts
    def changed(*args, **kwargs):
        system, user = real(*args, **kwargs)
        return system, user + "\nСинтетическое изменение инструкции сборщика."
    monkeypatch.setattr(consensus, "build_prompts", changed)
    second = run(source)
    assert first["prompt_hash"] == second["prompt_hash"]
    assert first["cache_path"] != second["cache_path"]
    assert second["usage"]["calls"] == 3 and second["skipped"] == 0


def test_rehearsal_isolated_ten_chats_bounded_concurrency_and_repeat(source, mock_model, tmp_path, capsys):
    folder = tmp_path / "data" / "reports" / "rehearsal"
    args = ["DEV", "--from-db", "--rehearsal", "--limit", "10", "--runs", "3", "--export-results",
            "--split", "prod", "--team-name", "Synthetic Team", "--report-dir", str(folder)]
    assert process_control_set.main(args) == 0
    capsys.readouterr()
    assert mock_model["calls"] == 30 and 1 < mock_model["peak"] <= 2
    assert not (tmp_path / "results" / "prod" / "results.json").exists()
    with closing(connect_db(source[0])) as conn:
        assert conn.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM conversations WHERE dataset_group_id='DEV'").fetchone()[0] == 12
    with closing(connect_db(folder / "rehearsal.sqlite3")) as conn:
        rows = conn.execute("SELECT conversation_id,channel,raw_json FROM conversations WHERE dataset_group_id='DEV_REHEARSAL'").fetchall()
        assert len(rows) == 10
        records = [{"conversation_id": row[0], "channel": row[1], "transcript": json.loads(row[2])["transcript"]} for row in rows]
    payload = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    assert inspect_submission(payload, split="prod", records=records, expected_models={"test-model"}) == []
    assert process_control_set.main(args) == 0
    capsys.readouterr()
    assert mock_model["calls"] == 30
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("control_report*.json")]
    assert sorted(report["usage"]["calls"] for report in reports) == [0, 30]


def test_limit_without_rehearsal_is_rejected_before_calls(source, mock_model):
    with pytest.raises(SystemExit):
        process_control_set.main(["DEV", "--from-db", "--limit", "10"])
    assert mock_model["calls"] == 0


def test_one_saved_evaluation_cannot_supply_three_votes(source, mock_model):
    report = run(source)
    card = report["evaluations"][0]
    with pytest.raises(SubmissionError) as exc:
        consensus.aggregate_three([card, card, card], card["conversation_snapshot"], card["checklist"], report["prompt_hash"])
    assert exc.value.code == "CONSENSUS_DUPLICATE_RUN"


def test_model_configuration_change_stops_before_next_paid_round(source, mock_model, monkeypatch):
    def steer(event):
        if event.get("round") == 1 and event["status"] == "completed":
            monkeypatch.setenv("META_MODEL", "different-test-model")
    with pytest.raises(SubmissionError) as exc:
        run(source, progress=steer)
    assert exc.value.code == "CONSENSUS_INPUT_CHANGED"
    assert mock_model["calls"] == 1


def test_unknown_replacement_criterion_stops_rehearsal_before_model(source, mock_model, tmp_path, capsys):
    with closing(connect_db(source[0])) as conn:
        changed = copy.deepcopy(get_checklist(conn))
        next(c for c in changed["criteria"] if c["criterion_id"] == "05")["criterion_id"] = "09"
        save_checklist(conn, changed)
    folder = tmp_path / "data" / "reports" / "wrong-checklist"
    result = process_control_set.main(["DEV", "--from-db", "--rehearsal", "--limit", "10", "--runs", "3",
        "--export-results", "--split", "prod", "--team-name", "Synthetic Team", "--report-dir", str(folder)])
    assert result == 1 and "SUBMISSION_CHECKLIST_MISMATCH" in capsys.readouterr().err
    assert mock_model["calls"] == 0
    assert not (folder / "results.json").exists()
