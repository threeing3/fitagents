import json

from algorithm.data.deduplicate import deduplicate_records
from algorithm.data.schemas import PreferencePair, TrainingExample
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
    assert TrainingExample.from_dict(json.loads(json.dumps(example.to_dict()))).example_id == "ex-1"


def test_validation_detects_duplicate_ids_and_unknown_source():
    rows = [
        {"example_id": "same", "task_type": "x", "user_message": "a", "source": "agent_trace", "split": "train"},
        {"example_id": "same", "task_type": "x", "user_message": "b", "source": "bad", "split": "train"},
    ]
    report = validate_training_rows(rows)
    assert report["error_count"] >= 2


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


def test_preference_pair_rejects_identical_candidates():
    pair = PreferencePair(prompt="q", chosen="same", rejected="same", source="expert_labeled")
    assert "chosen and rejected must differ" in pair.validate()
