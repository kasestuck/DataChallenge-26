"""Submission file safety and control-command integration with synthetic inputs."""
import json
from pathlib import Path

import pytest

from backend.submission import SubmissionError
from scripts import export_results


@pytest.fixture
def export_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(export_results, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_results, "read_environment", lambda: {})
    ids = [f"synthetic-control-{i}" for i in range(10)]
    values = [("contact", True), ("discovery", None), ("identification", None),
              ("consultation", 20), ("empathy", True), ("dialogue", 10),
              ("closure", True), ("critical", False)]
    payload = {"schema_version": "1.0", "team_name": "Synthetic Team",
               "model": {"mode": "external_api", "name": "test-model"},
               "results": [{"conversation_id": cid, "status": "ok", "criteria": [
                   {"criterion_id": name, "value": value, "rationale": "Синтетическое объяснение для проверки экспорта.", "evidence_turns": [1]}
                   for name, value in values], "score": {"final_score": 100.0, "critical_flag": False}} for cid in ids]}
    metadata = {"expected_ids": ids, "transcript_lengths": {cid: 1 for cid in ids}}
    return tmp_path, payload, metadata


def test_export_utf8_and_identical_repeat_does_not_rewrite(export_fixture):
    root, payload, metadata = export_fixture
    first = export_results.write_submission(payload, metadata, split="prod")
    target = Path(first["path"])
    original_time = target.stat().st_mtime_ns
    assert target == root / "results" / "prod" / "results.json"
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert not target.read_bytes().startswith(b"\xef\xbb\xbf")
    repeated = export_results.write_submission(payload, metadata, split="prod")
    assert repeated["changed"] is False
    assert repeated["sha256"] == first["sha256"]
    assert target.stat().st_mtime_ns == original_time


def test_different_result_requires_explicit_replacement_and_retains_backup(export_fixture):
    root, payload, metadata = export_fixture
    first = export_results.write_submission(payload, metadata, split="prod")
    target = Path(first["path"])
    original = target.read_bytes()
    payload["team_name"] = "Updated Synthetic Team"
    with pytest.raises(SubmissionError, match="уже существует"):
        export_results.write_submission(payload, metadata, split="prod")
    assert target.read_bytes() == original
    updated = export_results.write_submission(payload, metadata, split="prod", replace=True)
    backup = Path(updated["backup"])
    assert backup.is_relative_to(root / "data" / "reports" / "submission_backups")
    assert backup.read_bytes() == original
    assert json.loads(target.read_text(encoding="utf-8"))["team_name"] == "Updated Synthetic Team"
    assert not list(target.parent.glob("*.tmp"))


def test_secret_in_model_response_prevents_public_file(export_fixture, monkeypatch):
    root, payload, metadata = export_fixture
    secret = "unit-test-export-secret"
    monkeypatch.setattr(export_results, "read_environment", lambda: {"MODEL_API_KEY": secret})
    payload["results"][0]["criteria"][0]["rationale"] = secret
    with pytest.raises(SubmissionError) as exc:
        export_results.write_submission(payload, metadata, split="prod")
    assert exc.value.code == "EXPORT_SECRET_DETECTED"
    assert secret not in str(exc.value)
    assert not (root / "results").exists()


def test_invalid_score_is_rejected_before_writing(export_fixture):
    root, payload, metadata = export_fixture
    payload["results"][0]["score"]["final_score"] = 0.0
    with pytest.raises(SubmissionError):
        export_results.write_submission(payload, metadata, split="prod")
    assert not (root / "results").exists()


def test_missing_database_does_not_create_an_empty_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SubmissionError) as exc:
        export_results.export_stored_results(missing, group_id="PROD", split="prod", team_name="Synthetic Team")
    assert exc.value.code == "DATABASE_NOT_FOUND"
    assert not missing.exists()


def test_competing_exporter_is_rejected_without_removing_its_lock(export_fixture):
    root, payload, metadata = export_fixture
    lock = root / "results" / "prod" / ".results-export.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("another-process", encoding="ascii")
    with pytest.raises(SubmissionError) as exc:
        export_results.write_submission(payload, metadata, split="prod")
    assert exc.value.code == "EXPORT_LOCKED"
    assert lock.read_text(encoding="ascii") == "another-process"
    assert not (lock.parent / "results.json").exists()


def test_racing_creation_cannot_be_overwritten(export_fixture, monkeypatch):
    root, payload, metadata = export_fixture
    real_link = export_results.os.link
    def racing_link(source, destination):
        Path(destination).write_bytes(b"created by another writer")
        real_link(source, destination)
    monkeypatch.setattr(export_results.os, "link", racing_link)
    with pytest.raises(SubmissionError) as exc:
        export_results.write_submission(payload, metadata, split="prod")
    assert exc.value.code == "RESULTS_CHANGED"
    folder = root / "results" / "prod"
    assert (folder / "results.json").read_bytes() == b"created by another writer"
    assert not list(folder.glob("*.tmp"))
    assert not (folder / ".results-export.lock").exists()
