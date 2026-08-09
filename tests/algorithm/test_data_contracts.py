import json

from algorithm.data.deduplicate import deduplicate_records
from algorithm.data.schemas import OutcomeLabel, PreferencePair, ToolDecisionExample, TrainingExample
from algorithm.data.split_dataset import split_records
from algorithm.data.validate_dataset import validate_training_rows


def test_training_example_validation_and_roundtrip():
    example = TrainingExample(
        example_id="ex-1",
        task_type="coach_response",
        user_message="今天如何安排训练？",
        source="expert_labeled",
        split="test",
    )
    assert example.validate() == []
    restored = TrainingExample.from_dict(json.loads(json.dumps(example.to_dict())))
    assert restored.example_id == "ex-1"
    assert restored.feedback_id is None


def test_feedback_id_is_required_only_for_user_feedback_labels():
    without_feedback = TrainingExample(
        example_id="feedback-missing",
        task_type="coach_response",
        user_message="训练后膝盖疼，怎么办？",
        source="agent_trace",
        outcome=OutcomeLabel(label_source="user_feedback", label_confidence=0.9),
    )
    assert "feedback_id is required" in " ".join(without_feedback.validate())

    with_feedback = TrainingExample(
        example_id="feedback-present",
        task_type="coach_response",
        user_message="训练后膝盖疼，怎么办？",
        source="agent_trace",
        feedback_id="fb-001",
        outcome=OutcomeLabel(label_source="user_feedback", label_confidence=0.9),
    )
    assert with_feedback.validate() == []

    synthetic = TrainingExample(
        example_id="synthetic-no-feedback",
        task_type="coach_response",
        user_message="今天如何训练？",
        source="synthetic",
    )
    assert synthetic.validate() == []


def test_feedback_id_rejects_blank_values():
    example = TrainingExample(
        example_id="feedback-blank",
        task_type="coach_response",
        user_message="今天如何训练？",
        feedback_id="  ",
    )
    assert "feedback_id must be a non-empty string" in " ".join(example.validate())


def test_validation_detects_duplicate_ids_and_unknown_source():
    rows = [
        {"example_id": "same", "task_type": "x", "user_message": "a", "source": "agent_trace", "split": "train"},
        {"example_id": "same", "task_type": "x", "user_message": "b", "source": "bad", "split": "train"},
    ]
    report = validate_training_rows(rows)
    assert report["error_count"] >= 2


def test_validation_detects_user_split_leakage():
    rows = [
        {"example_id": "u-train", "user_hash": "same-user", "task_type": "x", "user_message": "a", "source": "agent_trace", "split": "train"},
        {"example_id": "u-test", "user_hash": "same-user", "task_type": "x", "user_message": "b", "source": "agent_trace", "split": "test"},
    ]
    report = validate_training_rows(rows)
    assert report["user_split_leaks"] == {"same-user": ["test", "train"]}
    assert report["error_count"] >= 1


def test_deduplicate_and_user_split_are_deterministic():
    rows = [
        {"user_hash": "u1", "user_message": "Hello", "assistant_response": "A", "task_type": "general"},
        {"user_hash": "u1", "user_message": " hello ", "assistant_response": "A", "task_type": "general"},
    ]
    unique, duplicate_count = deduplicate_records(rows, ("user_message", "assistant_response"))
    split, counts = split_records(unique)
    assert duplicate_count == 1
    assert len(split) == 1
    assert sum(counts.values()) == 1


def test_user_rows_stay_in_one_partition_across_scenarios():
    rows = [
        {"user_hash": "u-same", "user_message": "plan", "task_type": "training_plan"},
        {"user_hash": "u-same", "user_message": "recovery", "task_type": "recovery_check"},
        {"user_hash": "u-other", "user_message": "plan", "task_type": "training_plan"},
    ]
    split, _ = split_records(rows)
    same_user_splits = {row["split"] for row in split if row["user_hash"] == "u-same"}
    assert len(same_user_splits) == 1


def test_small_user_cohort_gets_all_evaluation_partitions_when_possible():
    rows = [{"user_hash": f"u-{index}", "user_message": str(index), "task_type": "general"} for index in range(6)]
    split, counts = split_records(rows)
    assert {row["split"] for row in split} == {"train", "validation", "test"}
    assert all(counts[name] > 0 for name in ("train", "validation", "test"))


def test_preference_pair_rejects_identical_candidates():
    pair = PreferencePair(prompt="q", chosen="same", rejected="same", source="expert_labeled")
    assert "chosen and rejected must differ" in pair.validate()


def test_tool_decision_contract_rejects_sequence_not_selected():
    example = ToolDecisionExample(user_message="plan", selected_tools=["context.build"], tool_sequence=["plan.generate"])
    assert any("missing from selected_tools" in error for error in example.validate())
