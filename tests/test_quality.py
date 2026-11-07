"""Quality metrics and orchestration tests use synthetic fixtures, never the real model."""
import asyncio
import copy
import hashlib
import json
from contextlib import closing
from pathlib import Path

import pytest

from backend.checklist import get_checklist, validate_checklist
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.quality import answer_distributions, jargon_checklist, repeatability, select_diverse, verify_partial_result
from backend.scoring import calculate_scores
from scripts import quality_check


def card(cid, language="RU", channel="agent chat", answer="yes", identity="na"):
    return {"conversation_id": cid, "status": "completed", "total": "100.00", "prompt_hash": None,
            "conversation_snapshot": {"language": language, "channel": channel,
                                      "transcript": [{"speaker": "operator", "text": "Synthetic reply."}]},
            "criteria": [{"criterion_id": "01", "answer": answer, "valid": True},
                         {"criterion_id": "03", "answer": identity, "valid": True}]}


def sample():
    return [card("synthetic-ru"), card("synthetic-kz", "KZ"), card("synthetic-en", "EN"),
            card("synthetic-call", "RU", "voice call"), card("synthetic-identity", identity="yes"),
            card("synthetic-extra-1"), card("synthetic-extra-2", "KZ"), card("synthetic-extra-3", "EN")]


def test_distributions_overlap_voice_and_keep_invalid_denominator():
    rows = sample()
    rows[0]["criteria"][0].update(answer=None, valid=False)
    report = answer_distributions(rows)
    assert report["groups"]["RU"]["dialogues"] == 4
    assert report["groups"]["voice_call"]["dialogues"] == 1
    values = report["groups"]["RU"]["criteria"]["01"]
    assert values["valid_count"] == 3 and values["missing_or_invalid"] == 1
    assert values["answers"] == {"yes": 3}
    assert values["percent_of_valid"] == {"yes": 100.0}


def test_anomalies_are_descriptive_and_report_language_sample_sizes():
    rows = [card("synthetic-r", identity="yes"), card("synthetic-k", "KZ", answer="no"), card("synthetic-e", "EN")]
    report = answer_distributions(rows)
    assert any(a["kind"] == "identification_na_share" and a["group"] == "RU" and a["percent"] == 0 for a in report["anomalies"])
    assert any(a["kind"] == "dominant_answer" for a in report["anomalies"])
    assert any(a["kind"] == "dominant_total" for a in report["anomalies"])
    gap = next(a for a in report["anomalies"] if a["kind"] == "language_gap")
    assert gap["sample_sizes"] == [1, 1]
    assert "не установлен" in gap["note"]


def test_repeatability_separates_missing_from_disagreement():
    base = [card("synthetic-a"), card("synthetic-b")]
    runs = [copy.deepcopy(base) for _ in range(3)]
    runs[2][0]["criteria"][0]["answer"] = "no"
    runs[1][1]["criteria"][0].update(answer=None, valid=False)
    result = repeatability(runs, ["synthetic-a", "synthetic-b"])
    stats = result["criteria"]["01"]
    assert stats["complete_triplets"] == 1
    assert stats["unanimous_percent"] == 0 and stats["coverage_percent"] == 50
    assert stats["valid_pairs"] == 4 and stats["matching_pairs"] == 2
    assert stats["pairwise_percent"] == 50
    assert len(result["differences"]) == 2
    assert result["criteria"]["03"]["unanimous_percent"] == 100


def test_select_five_distinct_including_applicable_identification():
    ids = select_diverse(sample(), 5, identification=True)
    assert len(set(ids)) == 5
    assert "synthetic-identity" in ids and "synthetic-call" in ids
    chosen = [c for c in sample() if c["conversation_id"] in ids]
    assert {e["conversation_snapshot"]["language"] for e in chosen} == {"RU", "KZ", "EN"}


def test_selection_reports_missing_category():
    with pytest.raises(ValueError, match="идентификация"):
        select_diverse([card("synthetic-only")], 5, identification=True)


def test_new_criterion_and_type_change_preserve_weight_and_other_definitions():
    source = json.loads((Path(__file__).resolve().parents[1] / "backend/configs/default_checklist.json").read_text(encoding="utf-8"))
    original = validate_checklist(source)
    yes_no = validate_checklist(jargon_checklist(original))
    scaled = validate_checklist(jargon_checklist(yes_no, scale=True))
    before = {c["criterion_id"]: c for c in original["criteria"]}
    after = {c["criterion_id"]: c for c in scaled["criteria"]}
    assert "05" not in after and after["09"]["type"] == "scale"
    assert set(after["09"]["scale"]) == {"0", "1", "2", "3", "4", "5"}
    assert after["09"]["weight"] == before["05"]["weight"]
    for cid in set(before) - {"05"}:
        assert before[cid] == after[cid]


def test_partial_verifier_recomputes_score_and_detects_changed_reused_answer():
    config = {"sections": [{"id": "s", "name": "Synthetic"}], "criteria": [
        {"criterion_id": "keep", "type": "yes_no", "weight": 10, "scored": True, "section": "s"},
        {"criterion_id": "09", "type": "scale", "weight": 10, "scored": True, "section": "s", "scale": {"0": "none", "5": "full"}}]}
    row = {"criterion_id": "keep", "answer": "yes", "reasoning": "Synthetic reason.", "evidence": [], "checked_range": None}
    baseline = {"criteria": [row]}
    evaluation = {"status": "completed", "checklist": config, "criteria": [copy.deepcopy(row), {"criterion_id": "09", "answer": "0"}],
                  "total": "50.00", "reevaluation": {"reused_criterion_ids": ["keep"], "evaluated_criterion_ids": ["09"]}}
    assert verify_partial_result(evaluation, baseline)["verified"]
    evaluation["criteria"][0]["reasoning"] = "Different synthetic reason."
    with pytest.raises(ValueError, match="отличается"):
        verify_partial_result(evaluation, baseline)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "quality.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [
            {"conversation_id": "synthetic-dev", "channel": "agent chat", "language": "RU",
             "transcript": [{"timestamp": "00:00", "speaker": "operator", "text": "Synthetic fixture."}]}]}})
        version_id = get_checklist(conn)["checklist_version_id"]
    return path, {"base_version_id": version_id, "prompt_hash": hashlib.sha256(quality_check.PROMPT_PATH.read_bytes()).hexdigest()}


def test_dev_skips_ready_and_reports_legacy_prompt_cohort(db, tmp_path, monkeypatch):
    path, state = db
    async def batch(database_path, request, progress=None):
        assert database_path == path and request["force"] is False
        assert request["conversation_ids"] == ["synthetic-dev"]
        return {"evaluations": [card("synthetic-dev")], "prompt_hashes": ["legacy_unknown"]}
    monkeypatch.setattr(quality_check, "run_batch_report", batch)
    report = asyncio.run(quality_check.run_phase("dev", tmp_path, path, "DEV", state))
    assert report["prompt_hash_cohorts"] == {"legacy_unknown": 1}
    assert report["distributions"]["groups"]["RU"]["dialogues"] == 1


def test_criterion_restores_active_version_after_batch_failure(db, tmp_path, monkeypatch):
    path, state = db
    monkeypatch.setattr(quality_check, "latest_completed", lambda *args: sample())
    async def fail(*args, **kwargs):
        raise ValueError("Synthetic failure")
    monkeypatch.setattr(quality_check, "run_batch_report", fail)
    with pytest.raises(ValueError, match="Synthetic failure"):
        asyncio.run(quality_check.run_phase("criterion", tmp_path, path, "DEV", state))
    with closing(connect_db(path)) as conn:
        assert get_checklist(conn)["checklist_version_id"] == state["base_version_id"]


def test_changed_prompt_prevents_mixed_repeat_series(db, tmp_path):
    path, state = db
    state["prompt_hash"] = "not-the-current-hash"
    with pytest.raises(ValueError, match="промпта"):
        asyncio.run(quality_check.run_phase("repeat", tmp_path, path, "DEV", state))


@pytest.mark.parametrize(("phase", "existing"), [("repeat", "repeat-run-2.json"), ("criterion", "criterion-scale.json")])
def test_existing_intermediate_capture_blocks_before_any_billable_call(db, tmp_path, monkeypatch, phase, existing):
    path, state = db
    (tmp_path / existing).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(quality_check, "latest_completed", lambda *args: sample())
    async def forbidden(*args, **kwargs):
        pytest.fail("Existing capture must stop the run before a model request")
    monkeypatch.setattr(quality_check, "run_batch_report", forbidden)
    with pytest.raises(ValueError, match="захват"):
        asyncio.run(quality_check.run_phase(phase, tmp_path, path, "DEV", state))


def test_edges_isolate_synthetic_records_and_preserve_longest_input(db, tmp_path, monkeypatch):
    path, state = db
    async def batch(scratch, request, progress=None):
        assert Path(scratch) != path and request["force"] is True
        with closing(connect_db(scratch)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 4
        return {"evaluations": [], "counts": {"completed": 0, "needs_review": 0, "failed": 4}}
    monkeypatch.setattr(quality_check, "run_batch_report", batch)
    report = asyncio.run(quality_check.run_phase("edges", tmp_path, path, "DEV", state))
    with closing(connect_db(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert len(report["cases"]) == 4
    assert next(case for case in report["cases"] if case["is_longest_dev"])["conversation_id"] == "synthetic-dev"
    assert next(case for case in report["cases"] if case["conversation_id"] == "synthetic-no-operator")["operator_messages"] == 0
