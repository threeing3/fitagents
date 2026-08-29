import json
from collections import Counter

from algorithm.data.multilabel_intent_dataset_factory import (
    SECONDARY_COMBINATIONS,
    build_multilabel_intent_examples,
)
from algorithm.data.validate_dataset import validate_training_rows
from algorithm.datasets.build_multilabel_intent_dataset import build_dataset
from algorithm.inference.intent_catalog import AgentIntentCatalog


def _decision(row):
    return json.loads(row["assistant_response"])


def test_dataset_covers_runtime_primary_and_required_secondary_labels():
    rows = [row.to_dict() for row in build_multilabel_intent_examples()]
    train = [row for row in rows if row["split"] == "train"]
    calibration = [row for row in rows if row["split"] == "validation"]
    required_secondary = {secondary for _, secondary, _ in SECONDARY_COMBINATIONS}

    assert {_decision(row)["primary_intent"] for row in train} == AgentIntentCatalog.VALID_INTENTS
    assert {
        _decision(row)["primary_intent"] for row in calibration
    } == AgentIntentCatalog.VALID_INTENTS
    assert {
        label for row in train for label in _decision(row)["secondary_intents"]
    } == required_secondary
    assert {
        label for row in calibration for label in _decision(row)["secondary_intents"]
    } == required_secondary


def test_dataset_has_disjoint_users_and_template_families():
    rows = [row.to_dict() for row in build_multilabel_intent_examples()]
    report = validate_training_rows(rows)

    assert report["error_count"] == 0
    assert report["user_split_leaks"] == {}
    assert report["template_family_split_leaks"] == {}


def test_default_dataset_has_minimum_support_and_explicit_claims():
    rows, manifest = build_dataset()
    train_secondary = Counter(
        label
        for row in rows
        if row["split"] == "train"
        for label in _decision(row)["secondary_intents"]
    )
    calibration_secondary = Counter(
        label
        for row in rows
        if row["split"] == "validation"
        for label in _decision(row)["secondary_intents"]
    )

    assert len(rows) == 696
    assert set(train_secondary) == {secondary for _, secondary, _ in SECONDARY_COMBINATIONS}
    assert min(train_secondary.values()) == 24
    assert min(calibration_secondary.values()) == 5
    assert manifest["claims"]["development_used_for_generation"] is False
    assert manifest["claims"]["sufficient_for_model_quality_claim"] is False


def test_dataset_generation_is_reproducible():
    first, first_manifest = build_dataset(train_per_family=2, calibration_per_family=2)
    second, second_manifest = build_dataset(train_per_family=2, calibration_per_family=2)

    assert first == second
    assert first_manifest == second_manifest
