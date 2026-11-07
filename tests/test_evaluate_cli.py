"""CLI behavior with test-only evaluators, never an outbound API request."""

import json
from copy import deepcopy

import pytest

from backend.config import ConfigurationError, DataSettings
from backend.meta_client import ModelRequestError
from scripts import evaluate_one


SECRET = "test-key-never-print"


@pytest.fixture
def report():
    return {
        "conversation_id": "test-dialogue", "evaluation_id": "evaluation-1",
        "checklist_version_id": "version-1", "status": "completed", "total": "75.00",
        "gross_violation": False,
        "checklist": {"criteria": [{"criterion_id": "contact", "name": "Контакт"}]},
        "criteria": [{"criterion_id": "contact", "answer": "yes", "points": "75.00", "effective_weight": "100.00",
                      "reasoning": "Оператор поздоровался.", "evidence": [{"message_id": "m1", "speaker": "operator", "quote": "Здравствуйте!"}],
                      "checked_range": None, "validation_errors": []}],
        "usage": {"calls": 1, "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                  "cost": None, "price_status": "price_not_set"},
        "validation_attempts": [{"attempt": 1, "call_id": "call-1", "errors": []}],
        "raw_responses": [{"content": '{"answer":"yes"}', "model": "test-model", "finish_reason": "stop"}],
        "llm_calls": [{"call_id": "call-1", "attempt": 1, "http_status": 200, "prompt_tokens": 10}],
        "conversation_snapshot": {"audio_url": "https://recording.example/audio?sig=private-recording-signature",
                                  "transcript": [{"message_id": "m1", "timestamp": "00:01", "speaker": "operator", "text": "Здравствуйте!"}]},
        "error": None,
    }


@pytest.fixture
def isolated_cli(monkeypatch, tmp_path, report):
    settings = DataSettings(tmp_path / "cli-test.db", "", "DEV")
    monkeypatch.setattr(evaluate_one, "load_data_settings", lambda: settings)
    monkeypatch.setattr(evaluate_one, "read_environment", lambda: {"MODEL_API_KEY": SECRET})
    monkeypatch.setattr(evaluate_one, "get_evaluation", lambda conn, eid: deepcopy(report))
    calls = []

    def evaluate(conn, cid, **kwargs):
        calls.append((cid, kwargs))
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        return {"evaluation_id": "evaluation-1"}

    monkeypatch.setattr(evaluate_one, "evaluate_conversation", evaluate)
    return settings, calls


def test_render_readable_criteria_and_missing_price(report):
    text = evaluate_one.render_report(report)
    for expected in ("Контакт", "Ответ: yes", "75.00", "100.00", "[m1] operator: Здравствуйте!", "всего 30", "цена не задана"):
        assert expected in text
    assert "ТРАНСКРИПТ" not in text


def test_audit_includes_transcript_raw_responses_and_each_call(report):
    text = evaluate_one.render_report(report, audit=True)
    assert "[m1] 00:01 operator: Здравствуйте!" in text
    assert report["raw_responses"][0]["content"] in text
    calls_text = text.split("ВСЕ ВЫЗОВЫ API", 1)[1]
    assert '"call_id": "call-1"' in calls_text


def test_cli_passes_language_and_checklist_to_evaluator(isolated_cli, capsys):
    settings, calls = isolated_cli
    assert evaluate_one.main(["test-dialogue", "--lang", "kk", "--checklist-version-id", "version-override"]) == 0
    assert calls == [("test-dialogue", {"checklist_version_id": "version-override", "output_language": "kk"})]
    assert settings.database_path.is_file()
    assert "Статус: completed" in capsys.readouterr().out


def test_configured_prices_with_incomplete_usage_are_not_called_unset(report):
    report["usage"].update(price_status="configured", cost=None, usage_complete=False)
    text = evaluate_one.render_report(report)
    assert "неизвестна: неполный учёт usage" in text
    assert "цена не задана" not in text


def test_saved_mode_never_invokes_evaluator(isolated_cli, capsys):
    _, calls = isolated_cli
    assert evaluate_one.main(["evaluation-1", "--saved", "--audit"]) == 0
    assert calls == []
    assert "ТРАНСКРИПТ" in capsys.readouterr().out


@pytest.mark.parametrize("status,code", [("completed", 0), ("needs_review", 2), ("failed", 1)])
def test_status_controls_exit_code(isolated_cli, report, status, code, capsys):
    report["status"] = status
    if status != "completed":
        report["total"] = None
    assert evaluate_one.main(["evaluation-1", "--saved"]) == code
    assert f"Статус: {status}" in capsys.readouterr().out


def test_export_preserves_audit_but_excludes_key_and_audio_url(isolated_cli, report, tmp_path, capsys):
    report["criteria"][0]["reasoning"] = f"Never reveal {SECRET}"
    report["raw_responses"][0]["content"] = SECRET
    output_dir = tmp_path / "reports"
    assert evaluate_one.main(["evaluation-1", "--saved", "--report-dir", str(output_dir)]) == 0
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    exported_json = next(output_dir.glob("*.json"))
    exported_text = next(output_dir.glob("*.txt"))
    for file in (exported_json, exported_text):
        content = file.read_text(encoding="utf-8")
        assert SECRET not in content
        assert "private-recording-signature" not in content
        assert "[REDACTED]" in content
    saved = json.loads(exported_json.read_text(encoding="utf-8"))
    assert "audio_url" not in saved["conversation_snapshot"]
    assert saved["llm_calls"][0]["call_id"] == "call-1"
    assert "audio_url" in report["conversation_snapshot"]  # Export must not mutate original.


def test_export_does_not_overwrite_existing_audit(isolated_cli, tmp_path, capsys):
    output_dir = tmp_path / "reports"
    arguments = ["evaluation-1", "--saved", "--report-dir", str(output_dir)]
    assert evaluate_one.main(arguments) == 0
    contents = {file.name: file.read_bytes() for file in output_dir.iterdir()}
    capsys.readouterr()
    assert evaluate_one.main(arguments) == 1
    assert {file.name: file.read_bytes() for file in output_dir.iterdir()} == contents


@pytest.mark.parametrize("exception", [
    ConfigurationError(f"Configuration includes {SECRET}"),
    ModelRequestError("MODEL_ACCESS_DENIED", f"Provider includes {SECRET}", 401),
    ValueError(f"Unexpected value contains {SECRET}"),
    RuntimeError(f"Unexpected failure contains {SECRET}"),
])
def test_cli_exception_output_never_contains_key(monkeypatch, isolated_cli, capsys, exception):
    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(evaluate_one, "evaluate_conversation", fail)
    assert evaluate_one.main(["test-dialogue"]) in (1, 2)
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert output.err
