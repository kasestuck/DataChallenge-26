import copy
import json
import sqlite3
from decimal import Decimal

import pytest

from backend.checklist import (
    SEED_PATH, ChecklistNotFoundError, ChecklistValidationError, activate_checklist,
    get_checklist, list_checklists, save_checklist, seed_checklist, validate_checklist,
)
from backend.db import connect_db, init_db


@pytest.fixture
def checklist_config():
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "checklists.sqlite3"
    init_db(path)
    connection = connect_db(path)
    try:
        yield connection
    finally:
        connection.close()


def test_seed_exactly_once(database):
    first = get_checklist(database)
    second = seed_checklist(database)
    assert first == second
    assert first["version"] == 1
    assert len(first["criteria"]) == 8
    assert Decimal(first["weight_sum"]) == 100
    assert len(list_checklists(database)) == 1
    assert first["criteria"][5]["scale"]["7"].startswith("Один заметный сбой")
    assert all(c["active"] and c["order"] == i for i, c in enumerate(first["criteria"], 1))


def test_save_creates_new_version_preserving_old(database):
    original = get_checklist(database)
    changed = copy.deepcopy(original)
    changed["criteria"][0]["name"] = "Новый критерий"
    changed["criteria"][0]["criterion_id"] = "custom-contact"
    changed["criteria"][0]["weight"] = 50
    new_version = save_checklist(database, changed)
    assert new_version["version"] == original["version"] + 1
    assert new_version["checklist_version_id"] != original["checklist_version_id"]
    assert new_version["criteria"][0]["criterion_id"] == "custom-contact"
    assert Decimal(new_version["weight_sum"]) == 140
    assert get_checklist(database) == new_version
    assert get_checklist(database, original["checklist_version_id"]) == original
    activate_checklist(database, original["checklist_version_id"])
    assert get_checklist(database) == original
    assert len(list_checklists(database)) == 2


def test_repeated_identical_save_still_creates_version(database):
    original = get_checklist(database)
    saved = save_checklist(database, original)
    assert saved["version"] == 2
    assert saved["criteria"] == original["criteria"]


def test_seed_does_not_reactivate_first_version(database):
    original = get_checklist(database)
    second = save_checklist(database, original)
    assert seed_checklist(database)["checklist_version_id"] == second["checklist_version_id"]


def test_immutable_versions_enforced_by_database(database):
    version_id = get_checklist(database)["checklist_version_id"]
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("UPDATE checklist_versions SET name='mutated' WHERE checklist_version_id=?", (version_id,))
    database.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("DELETE FROM checklist_versions WHERE checklist_version_id=?", (version_id,))
    database.rollback()


def test_missing_version_does_not_change_active(database):
    original = get_checklist(database)
    with pytest.raises(ChecklistNotFoundError):
        activate_checklist(database, "does-not-exist")
    assert get_checklist(database) == original


def test_bad_config_not_saved(database):
    original = get_checklist(database)
    invalid = copy.deepcopy(original)
    invalid["criteria"][0]["weight"] = -1
    with pytest.raises(ChecklistValidationError):
        save_checklist(database, invalid)
    assert len(list_checklists(database)) == 1
    assert get_checklist(database) == original


@pytest.mark.parametrize("field,value", [
    ("weight", -1), ("weight", "NaN"), ("weight", "Infinity"),
    ("weight", True), ("type", "unknown"), ("section", "missing-section"),
    ("allow_na", "false"), ("active", 1), ("order", True),
])
def test_rejects_invalid_criterion_fields(checklist_config, field, value):
    checklist_config["criteria"][0][field] = value
    with pytest.raises(ChecklistValidationError):
        validate_checklist(checklist_config)


def test_rejects_duplicate_ids(checklist_config):
    checklist_config["criteria"][1]["criterion_id"] = "01"
    with pytest.raises(ChecklistValidationError, match="criterion_id"):
        validate_checklist(checklist_config)


def test_rejects_duplicate_section_ids(checklist_config):
    checklist_config["sections"][1]["id"] = "s1"
    with pytest.raises(ChecklistValidationError, match="section.id"):
        validate_checklist(checklist_config)


@pytest.mark.parametrize("scale", [
    {"20": "only one step"}, {"bad": "bad", "20": "full"},
    {"NaN": "bad", "20": "full"}, {"-1": "negative", "20": "full"},
    {"0": "zero", "0.0": "duplicate"}, {"10": "ten", "10.0": "duplicate"},
])
def test_rejects_invalid_scales(checklist_config, scale):
    checklist_config["criteria"][3]["scale"] = scale
    with pytest.raises(ChecklistValidationError):
        validate_checklist(checklist_config)


@pytest.mark.parametrize("field,value", [("weight", 1), ("scored", True), ("allow_na", True)])
def test_flag_constraints(checklist_config, field, value):
    checklist_config["criteria"][7][field] = value
    with pytest.raises(ChecklistValidationError):
        validate_checklist(checklist_config)


@pytest.mark.parametrize("condition", [None, "", "   "])
def test_na_requires_condition(checklist_config, condition):
    checklist_config["criteria"][1]["na_condition"] = condition
    with pytest.raises(ChecklistValidationError):
        validate_checklist(checklist_config)


def test_weight_sum_only_active_scored(database):
    config = get_checklist(database)
    config["criteria"][0]["active"] = False
    config["criteria"][1]["scored"] = False
    saved = save_checklist(database, config)
    assert Decimal(saved["weight_sum"]) == 75


def test_validation_does_not_mutate_input(checklist_config):
    before = copy.deepcopy(checklist_config)
    normalized = validate_checklist(checklist_config)
    assert checklist_config == before
    assert normalized["criteria"][0]["description"] == ""
    assert normalized["criteria"][0]["active"] is True


def test_fractional_weights_preserved(database):
    config = get_checklist(database)
    config["criteria"][0]["weight"] = "0.123456789123456789"
    saved = save_checklist(database, config)
    assert saved["criteria"][0]["weight"] == "0.123456789123456789"
    assert Decimal(saved["weight_sum"]) == Decimal("90.123456789123456789")


def test_outer_transaction_can_rollback_save(database):
    original = get_checklist(database)
    database.execute("BEGIN IMMEDIATE")
    saved = save_checklist(database, original)
    assert get_checklist(database) == saved
    database.rollback()
    assert get_checklist(database) == original
    assert len(list_checklists(database)) == 1
