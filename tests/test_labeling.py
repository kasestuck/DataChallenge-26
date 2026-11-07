"""Synthetic local fixtures only; no provider or organizer data is used."""
import copy
import csv
import json
from contextlib import closing
from pathlib import Path

import pytest

from backend import labeling
from backend.checklist import get_checklist, save_checklist
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.labeling import HUMAN_ANSWER, HUMAN_COMMENT, LabelingError, export_labeling_sheet, labeling_agreement, select_evaluations
from backend.scoring import calculate_scores


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(labeling, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(labeling, "read_environment", lambda: {"MODEL_API_KEY": "unit-test-only-secret"})
    path = tmp_path / "data" / "labels.sqlite3"
    init_db(path)
    conn = connect_db(path)
    config = {"name": "Synthetic rubric", "sections": [{"id": "s", "name": "Synthetic section"}], "criteria": [
        {"criterion_id": "a", "name": "Synthetic criterion", "description": "Synthetic rule", "section": "s", "type": "yes_no", "weight": 10,
         "allow_na": True, "na_condition": "Synthetic non-applicability", "yes_text": "Synthetic action", "no_text": "Synthetic absence", "scored": True},
        {"criterion_id": "b", "name": "Synthetic flag", "section": "s", "type": "flag", "weight": 0,
         "allow_na": False, "yes_text": "Synthetic violation", "no_text": "Synthetic nonviolation", "scored": False},
    ]}
    checklist = save_checklist(conn, config)
    combinations = [("RU", "agent chat")] * 4 + [("KZ", "agent chat")] * 3 + [("EN", "agent chat")] * 3 + [("RU", "voice call")] * 2
    records = []
    for i, (language, channel) in enumerate(combinations):
        records.append({"conversation_id": f"synthetic-{i:02}", "language": language, "channel": channel,
                        "audio_url": "https://audio.example/should-not-be-exported",
                        "transcript": [{"timestamp": "00:00", "speaker": "client", "text": f"Synthetic client message {i}."},
                                       {"timestamp": "00:01", "speaker": "operator", "text": "Synthetic operator response."}]})
    import_dataset(conn, {"isSuccess": True, "error": "", "data": {"dataset_group_id": "DEV", "total_records": len(records), "records": records}})
    scores = calculate_scores(checklist, {"a": "yes", "b": "no"})
    for i, record in enumerate(records):
        snapshot = {**record, "revision": 1, "transcript": [{**message, "message_id": f"m{j+1}", "offset_sec": None} for j, message in enumerate(record["transcript"])]}
        criteria = [{**score, "valid": True, "reasoning": "Synthetic explanation.", "evidence": [{"message_id": "m2", "quote": "Synthetic operator response.", "speaker": "operator"}] if score["answer"] == "yes" else [],
                     "checked_range": {"from": "m1", "to": "m2"} if score["answer"] == "no" else None, "source": "model"} for score in scores["criteria"]]
        result = {"criteria": criteria, "total": scores["total"], "sections": scores["sections"], "gross_violation": False,
                  "status": "completed", "prompt_hash": "synthetic-hash"}
        conn.execute("""INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,
                        conversation_snapshot_json,status,original_result_json,final_result_json,created_at,updated_at,validation_attempts_json)
                        VALUES (?,?,?,1,?,'completed',?,?,'2026-01-02','2026-01-02',?)""",
                     (f"eval-{i:02}", record["conversation_id"], checklist["checklist_version_id"], json.dumps(snapshot),
                      json.dumps(result, default=str), json.dumps(result, default=str), json.dumps([{"attempt": 1}, {"attempt": 2}] if i == 3 else [{"attempt": 1}])))
    conn.commit()
    yield conn, checklist, tmp_path
    conn.close()


def read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def write_rows(path, fields, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_export_balances_languages_channel_prioritizes_retry_and_leaves_human_columns_blank(fixture):
    conn, checklist, root = fixture
    report = export_labeling_sheet(conn)
    assert report["conversations"] == 10 and report["rows"] == 20
    assert report["human_answers_filled"] == 0
    assert "synthetic-03" in report["conversation_ids"]
    assert {item["language"] for item in report["selection"]} == {"RU", "KZ", "EN"}
    assert {item["channel"] for item in report["selection"]} == {"agent chat", "voice call"}
    fields, rows = read_rows(report["csv_path"])
    assert all(row[HUMAN_ANSWER] == row[HUMAN_COMMENT] == "" for row in rows)
    assert Path(report["csv_path"]).read_bytes().startswith(b"\xef\xbb\xbf")
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert "Synthetic operator response." in markdown
    assert "audio.example" not in markdown + Path(report["csv_path"]).read_text(encoding="utf-8-sig")
    assert "[скрыто" not in markdown


def test_empty_labels_are_not_invented_as_zero_agreement(fixture):
    conn, _, _ = fixture
    report = labeling_agreement(export_labeling_sheet(conn)["csv_path"])
    assert report["overall"] == {"rows": 20, "labeled": 0, "comparable": 0, "matches": 0, "model_unavailable": 0,
                                 "annotation_coverage_percent": "0.00", "agreement_percent": None}
    assert report["disagreements"] == []


def test_partial_human_labels_denominators_and_discrepancies(fixture):
    conn, _, _ = fixture
    exported = export_labeling_sheet(conn)
    fields, rows = read_rows(exported["csv_path"])
    rows[0][HUMAN_ANSWER] = "yes"
    rows[1][HUMAN_ANSWER] = "yes"
    rows[1][HUMAN_COMMENT] = "Synthetic reviewer explanation."
    rows[2][HUMAN_ANSWER] = "no"
    rows[2]["model_answer"], rows[2]["model_valid"] = "", "false"
    write_rows(exported["csv_path"], fields, rows)
    report = labeling_agreement(exported["csv_path"])
    assert report["overall"]["annotation_coverage_percent"] == "15.00"
    assert report["overall"]["agreement_percent"] == "50.00"
    assert report["overall"]["model_unavailable"] == 1
    assert {item["kind"] for item in report["disagreements"]} == {"answer_mismatch", "model_unavailable"}
    assert next(item for item in report["per_criterion"] if item["criterion_id"] == "a")["agreement_percent"] == "100.00"


def test_mixed_prompt_versions_are_reported_without_invented_hash(fixture):
    conn, _, _ = fixture
    exported = export_labeling_sheet(conn)
    fields, rows = read_rows(exported["csv_path"])
    rows[0]["prompt_hash"] = ""
    rows[1]["prompt_hash"] = "known-synthetic-hash"
    write_rows(exported["csv_path"], fields, rows)
    report = labeling_agreement(exported["csv_path"])
    assert "unknown" in report["prompt_hash_rows"]
    assert report["prompt_hash_rows"]["known-synthetic-hash"] == 1
    assert len(report["warnings"]) == 2


@pytest.mark.parametrize("mutation", ["bad_human", "bad_model", "bad_allowed", "duplicate", "mixed_version", "empty_id"])
def test_invalid_manual_csv_is_rejected(fixture, mutation):
    conn, _, _ = fixture
    exported = export_labeling_sheet(conn)
    fields, rows = read_rows(exported["csv_path"])
    if mutation == "bad_human":
        rows[0][HUMAN_ANSWER] = "maybe"
    elif mutation == "bad_model":
        rows[0]["model_answer"] = "maybe"
    elif mutation == "bad_allowed":
        rows[0]["allowed_answers"] = '{"yes": true}'
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "mixed_version":
        rows[1]["checklist_version_id"] = "different-version"
    else:
        rows[0]["conversation_id"] = ""
    write_rows(exported["csv_path"], fields, rows)
    with pytest.raises(LabelingError):
        labeling_agreement(exported["csv_path"])


def test_export_rejects_paths_outside_ignored_reports_and_missing_evaluations(fixture):
    conn, _, root = fixture
    with pytest.raises(LabelingError, match="data/reports"):
        export_labeling_sheet(conn, output_dir=root / "public")
    assert not (root / "public").exists()
    with pytest.raises(LabelingError, match="доступно"):
        export_labeling_sheet(conn, count=13)


def test_export_uses_immutable_original_not_reviewed_final(fixture):
    conn, _, _ = fixture
    original = conn.execute("SELECT original_result_json FROM evaluations WHERE evaluation_id='eval-03'").fetchone()[0]
    final = json.loads(original)
    final["criteria"][0]["answer"] = "no"
    conn.execute("UPDATE evaluations SET final_result_json=? WHERE evaluation_id='eval-03'", (json.dumps(final),))
    conn.commit()
    report = export_labeling_sheet(conn)
    _, rows = read_rows(report["csv_path"])
    assert next(row for row in rows if row["conversation_id"] == "synthetic-03" and row["criterion_id"] == "a")["model_answer"] == "yes"


def test_export_skips_stale_revision_and_prioritizes_needs_review(fixture):
    conn, _, _ = fixture
    conn.execute("UPDATE conversations SET revision=2 WHERE conversation_id='synthetic-00'")
    conn.execute("UPDATE evaluations SET status='needs_review' WHERE evaluation_id='eval-02'")
    conn.commit()
    _, chosen = select_evaluations(conn)
    assert "synthetic-00" not in {item["conversation_id"] for item in chosen}
    assert "synthetic-02" in {item["conversation_id"] for item in chosen}
    assert "current_needs_review" in next(item["selection_reason"] for item in chosen if item["conversation_id"] == "synthetic-02")


@pytest.mark.parametrize("text", ["=SUM(1,1)", "+4", "-1", "@SUM(A1)", "\tformula", "\ntext", "'=text", "normal", "'normal"])
def test_csv_formula_escape_is_reversible(text):
    escaped = labeling._csv_safe(text)
    assert labeling._csv_restore(escaped) == text
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\n")):
        assert escaped.startswith("'")


def test_export_redacts_secret_and_escapes_markdown_html(fixture):
    conn, _, _ = fixture
    row = conn.execute("SELECT original_result_json,conversation_snapshot_json FROM evaluations WHERE evaluation_id='eval-03'").fetchone()
    original, snapshot = json.loads(row[0]), json.loads(row[1])
    original["criteria"][0]["reasoning"] = '=DANGEROUS("unit-test-only-secret")'
    snapshot["transcript"][0]["text"] = "<script>Synthetic HTML</script> unit-test-only-secret"
    conn.execute("UPDATE evaluations SET original_result_json=?,conversation_snapshot_json=? WHERE evaluation_id='eval-03'",
                 (json.dumps(original), json.dumps(snapshot)))
    conn.commit()
    exported = export_labeling_sheet(conn)
    csv_text = Path(exported["csv_path"]).read_text(encoding="utf-8-sig")
    md = Path(exported["markdown_path"]).read_text(encoding="utf-8")
    assert "unit-test-only-secret" not in csv_text + md
    assert "[REDACTED]" in csv_text + md
    assert "&lt;script&gt;" in md and "<script>" not in md
    _, rows = read_rows(exported["csv_path"])
    assert next(row for row in rows if row["conversation_id"] == "synthetic-03")["reasoning"].startswith("'=")


def test_reviewer_provenance_is_not_compared_as_model_answer(fixture):
    conn, _, _ = fixture
    row = conn.execute("SELECT original_result_json FROM evaluations WHERE evaluation_id='eval-03'").fetchone()
    original = json.loads(row[0])
    original["criteria"][0].update(source="reused", source_kind="reviewer")
    conn.execute("UPDATE evaluations SET original_result_json=? WHERE evaluation_id='eval-03'", (json.dumps(original),))
    conn.commit()
    exported = export_labeling_sheet(conn)
    _, rows = read_rows(exported["csv_path"])
    row = next(r for r in rows if r["conversation_id"] == "synthetic-03" and r["criterion_id"] == "a")
    assert row["model_answer"] == "" and row["model_valid"] == "false"
