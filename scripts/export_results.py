"""Export stored CQA evaluations to the organizers' JSON 1.0 format; no LLM calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, PROJECT_ROOT, load_data_settings, read_environment
from backend.submission import SubmissionError, build_submission, validate_submission


def write_submission(payload, metadata, *, split, replace=False):
    """Validate before publishing a file; retain an ignored backup on explicit replacement."""
    if split not in {"dev", "prod"}:
        raise SubmissionError("INVALID_SPLIT", "Допустимы только dev или prod.")
    validate_submission(payload, expected_ids=metadata["expected_ids"],
                        transcript_lengths=metadata["transcript_lengths"])
    content = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    secret = read_environment().get("MODEL_API_KEY") or ""
    if secret and secret.encode("utf-8") in content:
        raise SubmissionError("EXPORT_SECRET_DETECTED", "В результатах обнаружено значение API-ключа. Файл не сохранён.")
    path = PROJECT_ROOT / "results" / split / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / ".results-export.lock"
    try:
        with lock.open("x", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        raise SubmissionError("EXPORT_LOCKED", "Экспорт уже выполняется либо остался lock после прерывания. Проверьте процесс перед удалением .results-export.lock.") from None
    try:
        return _publish_locked(path, content, split=split, replace=replace)
    finally:
        lock.unlink()  # This call exclusively created the lock above.


def _publish_locked(path, content, *, split, replace):
    """Serialize this exporter's writers; atomically refuse an existing new target."""
    existing = path.read_bytes() if path.exists() else None
    digest = hashlib.sha256(content).hexdigest()
    if existing == content:
        return {"path": str(path), "sha256": digest, "changed": False, "backup": None}
    if existing is not None and not replace:
        raise SubmissionError("RESULTS_ALREADY_EXIST", "results.json уже существует и отличается. Проверьте его и используйте --replace для замены с резервной копией.")
    backup = None
    if existing is not None:
        folder = PROJECT_ROOT / "data" / "reports" / "submission_backups"
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / (split + "-" + uuid4().hex + ".json")
        with backup.open("xb") as handle:
            handle.write(existing)
    temporary = path.parent / (".results-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # Detect external changes before an explicitly authorized replacement.
        current = path.read_bytes() if path.exists() else None
        if current != existing:
            raise SubmissionError("RESULTS_CHANGED", "results.json изменён другим процессом. Повторите проверку.")
        if existing is None:
            try:
                os.link(temporary, path)  # Atomic creation, never overwrites a racing writer.
            except FileExistsError:
                raise SubmissionError("RESULTS_CHANGED", "results.json создан другим процессом. Его содержимое сохранено.") from None
        else:
            os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only the temporary file created by this call.
    return {"path": str(path), "sha256": digest, "changed": True,
            "backup": str(backup) if backup else None}


def export_stored_results(database_path, *, group_id, split, team_name,
                          checklist_version_id=None, conversation_ids=None, replace=False):
    path = Path(database_path).resolve()
    if not path.is_file():
        raise SubmissionError("DATABASE_NOT_FOUND", "База данных не найдена; загрузите и оцените набор.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        # All selected records and their evaluations belong to one read snapshot.
        conn.execute("BEGIN")
        payload, metadata = build_submission(conn, group_id=group_id, split=split,
            team_name=team_name, checklist_version_id=checklist_version_id,
            conversation_ids=conversation_ids)
    written = write_submission(payload, metadata, split=split, replace=replace)
    return {**metadata, **written, "split": split, "team_name": team_name,
            "model": payload["model"], "status_counts": {
                "ok": sum(r["status"] == "ok" for r in payload["results"]),
                "error": sum(r["status"] == "error" for r in payload["results"])}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group_id")
    parser.add_argument("--split", choices=["dev", "prod"], required=True)
    parser.add_argument("--team-name", required=True)
    parser.add_argument("--checklist-version-id")
    parser.add_argument("--replace", action="store_true", help="Заменить изменившийся файл, сохранив прежний в data/reports/submission_backups.")
    args = parser.parse_args(argv)
    try:
        settings = load_data_settings()
        report = export_stored_results(settings.database_path, group_id=args.group_id,
            split=args.split, team_name=args.team_name,
            checklist_version_id=args.checklist_version_id, replace=args.replace)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if report["status_counts"]["error"] else 0
    except (ConfigurationError, SubmissionError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "CONFIGURATION_ERROR"),
                                   "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception:
        print('{"error":{"code":"EXPORT_FAILED","message":"Выгрузка не завершена. Предыдущий файл не подменён фиктивными результатами."}}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
