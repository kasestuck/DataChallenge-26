"""Control/demo workflows with synthetic data and model mocks confined to tests."""
import asyncio
import json
from contextlib import closing
from dataclasses import replace
from decimal import Decimal

import pytest

from backend import batch_reporting, llm_client
from backend.batch_reporting import run_batch_report
from backend.checklist import get_checklist, save_checklist
from backend.config import DataSettings
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from scripts import demo_check, process_control_set


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-model-key")
    monkeypatch.setenv("META_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("META_MODEL", "test-model")
    monkeypatch.setenv("PRICE_INPUT_PER_1M", "")
    monkeypatch.setenv("PRICE_OUTPUT_PER_1M", "")
    monkeypatch.setenv("MAX_CONCURRENCY", "2")
    monkeypatch.setattr(batch_reporting, "PROJECT_ROOT", tmp_path)
    path = tmp_path / "source.sqlite3"
    init_db(path)
    records = [{"conversation_id": cid, "language": language, "channel": "agent chat", "audio_url": None,
                "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Расскажите об общих условиях обслуживания."},
                               {"timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте, я тестовый консультант. Общие условия опубликованы на сайте."},
                               {"timestamp": "00:02", "speaker": "operator", "text": "Всего доброго."}]} for cid, language in [("synthetic-a", "RU"), ("synthetic-b", "EN")]]
    envelope = {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 2, "records": records}}
    with closing(connect_db(path)) as conn:
        import_dataset(conn, envelope)
    settings = DataSettings(path, "", "DEV")
    monkeypatch.setattr(process_control_set, "load_data_settings", lambda: settings)
    monkeypatch.setattr(demo_check, "load_data_settings", lambda: settings)
    return path, settings, envelope


def valid_reply(user):
    decoder = json.JSONDecoder()
    metadata = decoder.raw_decode(user.split("Данные диалога:\n", 1)[1])[0]
    checklist = decoder.raw_decode(user.split("Чек-лист JSON (только активные критерии):\n", 1)[1])[0]
    criteria = []
    for c in checklist["criteria"]:
        answer = "no" if c["type"] == "flag" else max(c["scale"], key=Decimal) if c["type"] == "scale" else "yes"
        reasoning = "Действие подтверждено синтетической репликой."
        if c["criterion_id"] == "03":
            answer = "na"
            reasoning = c["na_condition"] + " Синтетический запрос содержит общий вопрос."
        criteria.append({"criterion_id": c["criterion_id"], "answer": answer, "reasoning": reasoning,
                         "evidence": [] if answer == "no" else [{"message_id": "m2", "quote": "Здравствуйте, я тестовый консультант."}],
                         "checked_range": {"from": "m1", "to": "m3"} if answer == "no" else None})
    return {"conversation_id": metadata["conversation_id"], "criteria": criteria}


@pytest.fixture
def model_mock(monkeypatch):
    calls = []
    def transport(system, user, **kwargs):
        result = valid_reply(user)
        calls.append(result)
        return {"content": json.dumps(result, ensure_ascii=False), "model": "test-model", "http_status": 200,
                "finish_reason": "stop", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    monkeypatch.setattr(llm_client, "request_completion", transport)
    return calls


def test_batch_report_new_usage_and_skipped_cost_are_distinct(environment, model_mock):
    path, _, _ = environment
    events = []
    first = asyncio.run(run_batch_report(path, {"conversation_ids": ["synthetic-a", "synthetic-b"]}, progress=events.append))
    assert first["counts"] == {"completed": 2, "needs_review": 0, "failed": 0}
    assert first["usage"]["calls"] == 2 and first["usage"]["total_tokens"] == 60
    assert first["usage"]["cost"] is None
    assert events[0]["status"] == "pending" and events[-1]["status"] == "completed"
    second = asyncio.run(run_batch_report(path, {"conversation_ids": ["synthetic-a", "synthetic-b"]}))
    assert second["skipped"] == 2 and second["usage"]["calls"] == second["usage"]["total_tokens"] == 0
    assert second["usage"]["cost"] == "0"
    assert second["averages_new"]["evaluation_count"] == 0
    assert Decimal(second["averages_selected"]["total_tokens"]) == 30
    assert len(model_mock) == 2


def test_batch_report_separates_needs_review_from_failed(environment, monkeypatch):
    path, _, _ = environment
    def transport(system, user, **kwargs):
        result = valid_reply(user)
        if result["conversation_id"] == "synthetic-a":
            result["criteria"][0]["evidence"][0]["message_id"] = "absent"
        return {"content": json.dumps(result), "model": "test-model", "http_status": 200, "finish_reason": "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    monkeypatch.setattr(llm_client, "request_completion", transport)
    result = asyncio.run(run_batch_report(path, {"conversation_ids": ["synthetic-a", "synthetic-b", "missing"]}))
    assert result["counts"] == {"completed": 1, "needs_review": 1, "failed": 1}
    assert result["usage"]["calls"] == 3 and result["validation_retries"] == 1
    assert result["needs_review"][0]["validation_errors"]
    assert result["ready_conversation_ids"] == ["synthetic-b"]


def test_control_missing_url_does_not_call_model(environment, model_mock, capsys):
    assert process_control_set.main(["DEV"]) == 1
    assert "DATASET_NOT_CONFIGURED" in capsys.readouterr().err
    assert not model_mock


def test_control_from_db_can_be_repeated_safely(environment, model_mock, capsys):
    folder = environment[0].parent / "data" / "reports" / "control-repeated"
    args = ["DEV", "--from-db", "--report-dir", str(folder)]
    assert process_control_set.main(args) == 0
    assert len(model_mock) == 2
    assert process_control_set.main(args) == 0
    assert len(model_mock) == 2
    assert len(list(folder.glob("control_report*.json"))) == 2
    output = capsys.readouterr().out
    assert '"network_load_verified": false' in output
    assert '"source": "local_database"' in output


def test_control_network_processes_only_returned_ids(environment, model_mock, monkeypatch, capsys):
    _, settings, envelope = environment
    monkeypatch.setattr(process_control_set, "load_data_settings", lambda: replace(settings, api_url="https://dataset.example/transcripts"))
    envelope["data"]["records"] = envelope["data"]["records"][:1]
    envelope["data"]["total_records"] = 1
    monkeypatch.setattr(process_control_set, "fetch_dataset", lambda *args: envelope)
    assert process_control_set.main(["DEV"]) == 0
    assert len(model_mock) == 1 and model_mock[0]["conversation_id"] == "synthetic-a"


def test_control_submission_checks_count_before_model_call(environment, model_mock, capsys):
    assert process_control_set.main(["DEV", "--from-db", "--export-results", "--split", "dev",
                                     "--team-name", "Synthetic Team"]) == 1
    assert "SUBMISSION_COUNT_MISMATCH" in capsys.readouterr().err
    assert not model_mock


def test_control_exports_error_records_even_when_batch_has_failures(environment, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(process_control_set, "validate_group_records", lambda records, split: None)
    async def failed_batch(*args, **kwargs):
        return {"status": "completed", "counts": {"completed": 1, "failed": 1, "needs_review": 0}}
    monkeypatch.setattr(process_control_set, "run_batch_report", failed_batch)
    def capture(database_path, **kwargs):
        seen.append(kwargs)
        return {"status_counts": {"ok": 1, "error": 1}}
    monkeypatch.setattr(process_control_set, "export_stored_results", capture)
    assert process_control_set.main(["DEV", "--from-db", "--export-results", "--split", "dev",
                                     "--team-name", "Synthetic Team"]) == 2
    assert len(seen) == 1
    assert seen[0]["conversation_ids"] == ["synthetic-a", "synthetic-b"]
    assert seen[0]["checklist_version_id"]
    assert '"error": 1' in capsys.readouterr().out


def test_report_paths_cannot_escape_ignored_directory(environment):
    path, _, _ = environment
    with pytest.raises(ValueError, match="data/reports"):
        batch_reporting.make_report_directory("control", path.parent / "public")


def test_demo_default_missing_url_is_honest(environment, model_mock, capsys):
    assert demo_check.main([]) == 1
    output = capsys.readouterr().out
    assert "DATASET_API_URL" in output
    assert all("ОШИБКА — " + name in output for name in ("загрузка", "оценка", "апелляция и решение", "замена критерия", "стоимость"))
    assert not model_mock


def test_demo_runs_api_and_preserves_source_database(environment, model_mock, capsys):
    path, _, _ = environment
    with closing(connect_db(path)) as conn:
        before = get_checklist(conn)["checklist_version_id"]
    assert demo_check.main(["--from-db", "--conversation-id", "synthetic-a"]) == 0
    assert len(model_mock) == 3
    with closing(connect_db(path)) as conn:
        assert get_checklist(conn)["checklist_version_id"] == before
        assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0] == 0
    output = capsys.readouterr().out
    assert "ОК — апелляция и решение" in output
    assert "ОК — замена критерия" in output
    assert "внешний API не проверен" in output


def test_demo_reusing_report_directory_does_not_overwrite_or_mask_error(environment, model_mock, capsys):
    folder = environment[0].parent / "data" / "reports" / "same-demo"
    args = ["--from-db", "--conversation-id", "synthetic-a", "--report-dir", str(folder)]
    assert demo_check.main(args) == 0
    original_report = (folder / "demo_report.json").read_bytes()
    model_calls = len(model_mock)
    assert demo_check.main(args) == 1
    assert len(model_mock) == model_calls
    assert (folder / "demo_report.json").read_bytes() == original_report
    output = capsys.readouterr().out
    assert "Выберите новую папку отчёта" in output
    assert "REPORT_EXISTS" in output
    assert "Traceback" not in output


def test_network_demo_copies_active_checklist_but_no_stale_records(environment, model_mock, monkeypatch, capsys):
    from backend import dataset
    import backend.main as api_main
    path, settings, envelope = environment
    with closing(connect_db(path)) as conn:
        active = get_checklist(conn)
        active["criteria"][4]["description"] = "Синтетическая дополнительная инструкция активной версии."
        before = save_checklist(conn, active)
    updated_settings = replace(settings, api_url="https://dataset.example/transcripts")
    monkeypatch.setattr(demo_check, "load_data_settings", lambda: updated_settings)
    monkeypatch.setattr(api_main, "load_data_settings", lambda: updated_settings)
    envelope["data"]["records"] = envelope["data"]["records"][:1]
    envelope["data"]["total_records"] = 1
    monkeypatch.setattr(dataset, "fetch_dataset", lambda *args, **kwargs: envelope)
    folder = path.parent / "data" / "reports" / "network-demo"
    assert demo_check.main(["--report-dir", str(folder)]) == 0
    report = json.loads((folder / "demo_report.json").read_text(encoding="utf-8"))
    assert report["network_load_verified"]
    assert report["checklist_copy"]["source_version_id"] == before["checklist_version_id"]
    evaluation = json.loads((folder / "evaluation.json").read_text(encoding="utf-8"))["body"]
    assert evaluation["checklist"]["criteria"][4]["description"] == before["criteria"][4]["description"]
    with closing(connect_db(folder / "demo.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    with closing(connect_db(path)) as conn:
        assert get_checklist(conn)["checklist_version_id"] == before["checklist_version_id"]
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
