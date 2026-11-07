from copy import deepcopy
from decimal import Decimal

import pytest

from backend.scoring import ScoringError, calculate_scores


def criterion(cid, weight, *, kind="yes_no", allow_na=True, section="s1", **kwargs):
    result = {
        "criterion_id": cid, "type": kind, "weight": weight, "allow_na": allow_na,
        "scored": kind != "flag", "active": True, "section": section,
    }
    result.update(kwargs)
    return result


def checklist(*criteria):
    return {"criteria": list(criteria), "sections": [{"id": "s1", "name": "Section 1"}, {"id": "s2", "name": "Section 2"}]}


@pytest.fixture
def full_checklist():
    return checklist(
        criterion("01", 10, allow_na=False),
        criterion("02", 15),
        criterion("03", 20, section="s2"),
        criterion("04", 20, kind="scale", scale={"20": "full", "15": "partial", "10": "half", "5": "hint", "0": "none"}, section="s2"),
        criterion("05", 15, allow_na=False),
        criterion("06", 15, kind="scale", allow_na=False, scale={"10": "full", "7": "one error", "4": "unclear", "0": "none"}),
        criterion("07", 5),
        criterion("08", 0, kind="flag", allow_na=False),
    )


@pytest.fixture
def full_answers():
    return {"01": "yes", "02": "yes", "03": "yes", "04": "20", "05": "yes", "06": "10", "07": "yes", "08": "no"}


def test_all_positive_answers_get_exact_100(full_checklist, full_answers):
    result = calculate_scores(full_checklist, full_answers)
    assert result["total"] == Decimal("100.00")
    assert result["total"].as_tuple().exponent == -2
    assert result["gross_violation"] is False


def test_multiple_na_redistribute_remaining_weights(full_checklist, full_answers):
    full_answers.update({"02": "na", "03": "na", "01": "no"})
    result = calculate_scores(full_checklist, full_answers)
    rows = {row["criterion_id"]: row for row in result["criteria"]}
    assert result["total"] == Decimal("84.62")  # 55 / 65 * 100
    assert rows["01"]["effective_weight"] == Decimal("15.38")
    assert rows["01"]["points"] == Decimal("0.00")
    assert rows["04"]["points"] == Decimal("30.77")
    assert rows["02"]["earned"] is None
    assert rows["02"]["effective_weight"] == Decimal("0.00")
    assert rows["02"]["points"] == Decimal("0.00")


def test_all_na_has_no_total():
    config = checklist(criterion("a", 10), criterion("b", 20))
    result = calculate_scores(config, {"a": "na", "b": "na"})
    assert result["total"] is None
    assert all(section["section_score"] is None for section in result["sections"])


def test_15_out_of_20_earns_75_percent_of_weight():
    config = checklist(criterion("custom", 40, kind="scale", scale={"0": "none", "15": "partial", "20": "full"}))
    result = calculate_scores(config, {"custom": "15"})
    assert result["criteria"][0]["earned"] == Decimal("30.00")
    assert result["criteria"][0]["effective_weight"] == Decimal("100.00")
    assert result["total"] == Decimal("75.00")


def test_scale_max_is_from_config_not_criterion_id(full_checklist, full_answers):
    full_answers["06"] = "7"
    result = calculate_scores(full_checklist, full_answers)
    row = next(row for row in result["criteria"] if row["criterion_id"] == "06")
    assert row["earned"] == Decimal("10.50")
    assert result["total"] == Decimal("95.50")


def test_round_half_up_without_rounding_intermediate_earned_values():
    config = checklist(criterion("custom", 1, kind="scale", scale={"0": "none", "1": "partial", "32": "full"}))
    result = calculate_scores(config, {"custom": "1"})
    assert result["criteria"][0]["earned"] == Decimal("0.03")
    assert result["criteria"][0]["points"] == Decimal("3.13")
    assert result["total"] == Decimal("3.13")  # exact result before rounding is 3.125
    assert result["sections"][0]["section_score"] == Decimal("3.13")


def test_gross_violation_never_changes_score(full_checklist, full_answers):
    baseline = calculate_scores(full_checklist, full_answers)
    full_answers["08"] = "yes"
    flagged = calculate_scores(full_checklist, full_answers)
    assert flagged["gross_violation"] is True
    assert flagged["total"] == baseline["total"]
    assert flagged["sections"] == baseline["sections"]
    assert flagged["criteria"][-1]["earned"] is None


def test_custom_flag_also_sets_violation_without_numeric_penalty():
    config = checklist(criterion("anything", 7), criterion("new_flag", 0, kind="flag", allow_na=False))
    result = calculate_scores(config, {"anything": "yes", "new_flag": "yes"})
    assert result["gross_violation"] is True
    assert result["total"] == Decimal("100.00")


def test_zero_weights_do_not_produce_division_by_zero():
    result = calculate_scores(checklist(criterion("zero", 0)), {"zero": "yes"})
    assert result["total"] is None
    assert result["criteria"][0]["effective_weight"] is None
    assert result["criteria"][0]["points"] is None


def test_configured_fractional_weights_remain_exact():
    config = checklist(criterion("small", "0.001"), criterion("other", "0.002"))
    result = calculate_scores(config, {"small": "yes", "other": "no"})
    assert result["criteria"][0]["weight"] == Decimal("0.001")
    assert result["criteria"][1]["weight"] == Decimal("0.002")
    assert result["sections"][0]["applicable_weight"] == Decimal("0.003")
    assert result["total"] == Decimal("33.33")


def test_section_scores_use_their_own_applicable_weights():
    config = checklist(criterion("a", 10), criterion("b", 30), criterion("c", 50, section="s2"))
    result = calculate_scores(config, {"a": "yes", "b": "no", "c": "na"})
    assert result["total"] == Decimal("25.00")
    assert result["sections"][0] == {
        "section_id": "s1", "section_score": Decimal("25.00"),
        "earned": Decimal("10.00"), "applicable_weight": Decimal("40.00"),
    }
    assert result["sections"][1]["section_score"] is None


def test_inactive_and_unscored_criteria_do_not_contribute():
    config = checklist(criterion("a", 10), criterion("b", 500, active=False), criterion("c", 900, scored=False))
    result = calculate_scores(config, {"a": "yes", "c": "no"})
    assert result["total"] == Decimal("100.00")
    assert [row["criterion_id"] for row in result["criteria"]] == ["a", "c"]
    assert result["criteria"][1]["points"] == Decimal("0.00")


def test_function_does_not_mutate_inputs(full_checklist, full_answers):
    original_config = deepcopy(full_checklist)
    original_answers = deepcopy(full_answers)
    calculate_scores(full_checklist, full_answers)
    assert full_checklist == original_config
    assert full_answers == original_answers


@pytest.mark.parametrize("answers", [{}, {"a": "yes", "extra": "no"}, {"a": True}, {"a": "maybe"}, {"a": "na"}])
def test_missing_extra_or_invalid_answers_are_rejected(answers):
    with pytest.raises(ScoringError):
        calculate_scores(checklist(criterion("a", 10, allow_na=False)), answers)


def test_intermediate_scale_value_is_rejected():
    config = checklist(criterion("a", 10, kind="scale", scale={"0": "no", "10": "full"}))
    with pytest.raises(ScoringError, match="configured scale step"):
        calculate_scores(config, {"a": "5"})


def test_flag_na_is_rejected_even_if_config_allows_na():
    with pytest.raises(ScoringError, match="N/A"):
        calculate_scores(checklist(criterion("a", 0, kind="flag", allow_na=True)), {"a": "na"})


@pytest.mark.parametrize("weight", [-1, "NaN", "Infinity", True])
def test_invalid_weight_is_rejected(weight):
    with pytest.raises(ScoringError):
        calculate_scores(checklist(criterion("a", weight)), {"a": "yes"})


def test_empty_checklist_returns_null():
    assert calculate_scores(checklist(), {})["total"] is None
