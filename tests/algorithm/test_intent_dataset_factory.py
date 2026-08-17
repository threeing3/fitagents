import json

import pytest

from algorithm.data.intent_dataset_factory import build_intent_examples
from algorithm.data.schemas import TrainingExample
from algorithm.data.validate_dataset import validate_training_rows
from algorithm.datasets.build_intent_dataset import build_dataset
from algorithm.datasets.build_sft_dataset import build_sft_rows


def test_intent_factory_is_reproducible_and_covers_target_weaknesses():
    first = [row.to_dict() for row in build_intent_examples(per_family=3)]
    second = [row.to_dict() for row in build_intent_examples(per_family=3)]

    assert first == second
    assert {row["quality_labels"]["weakness"] for row in first} == {
        "risk_level",
        "clarification",
        "multi_intent_priority",
        "memory_conflict",
    }


def test_intent_factory_keeps_template_families_and_users_split_disjoint():
    rows = [row.to_dict() for row in build_intent_examples(per_family=5)]
    report = validate_training_rows(rows)

    assert report["error_count"] == 0
    assert report["user_split_leaks"] == {}
    assert report["template_family_split_leaks"] == {}
    assert {row["split"] for row in rows} == {"train", "validation", "test"}
    assert all(not row["training_eligible"] for row in rows if row["split"] == "test")


def test_training_eligibility_requires_provenance_and_family():
    row = TrainingExample(
        example_id="bad-training-row",
        task_type="intent_decision_v2",
        user_message="帮我判断意图",
        split="train",
        training_eligible=True,
    )

    errors = " ".join(row.validate())
    assert "known label_source" in errors
    assert "template_family" in errors


def test_builder_rejects_exact_fixed_eval_collision(tmp_path):
    sample = build_intent_examples(per_family=1)[0]
    eval_path = tmp_path / "protected.json"
    eval_path.write_text(
        json.dumps([{"user_message": sample.user_message}], ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="fixed-evaluation collision"):
        build_dataset(per_family=1, eval_paths=[eval_path])


def test_builder_manifest_makes_synthetic_limitations_explicit(tmp_path):
    eval_path = tmp_path / "protected.json"
    eval_path.write_text("[]", encoding="utf-8")

    rows, manifest = build_dataset(per_family=2, eval_paths=[eval_path])

    assert len(rows) == 40
    assert manifest["exact_eval_collisions"] == 0
    assert manifest["template_family_split_leaks"] == {}
    assert manifest["claims"]["sufficient_for_model_quality_claim"] is False
    assert manifest["claims"]["expert_labeled"] is False


def test_sft_builder_excludes_ineligible_and_held_out_rows():
    rows = [row.to_dict() for row in build_intent_examples(per_family=1)]
    sft_rows = build_sft_rows(rows, include_splits={"train", "validation", "test"})

    assert len(sft_rows) == 18
    assert all(
        row["template_family"] not in {"risk_uncertain", "memory_unclear_time"} for row in sft_rows
    )
