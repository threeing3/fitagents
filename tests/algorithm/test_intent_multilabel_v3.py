import json

from algorithm.data.multi_secondary_intent_factory import build_multi_secondary_examples
from algorithm.data.validate_dataset import validate_training_rows
from algorithm.datasets.build_intent_multilabel_v3 import build_dataset


def _base_row(message: str = "帮我安排训练") -> dict:
    return {
        "example_id": "base-1",
        "task_type": "intent_decision_v2",
        "user_message": message,
        "user_hash": "base-user",
        "session_hash": "base-session",
        "assistant_response": json.dumps(
            {
                "primary_intent": "training_plan",
                "secondary_intents": [],
                "risk_level": "low",
                "needs_clarification": False,
            }
        ),
        "intent_label": "training_plan",
        "risk_label": "low",
        "quality_labels": {"review_status": "not_reviewed"},
        "label_source": "deterministic_rule_template",
        "template_family": "base-train",
        "human_review_status": "not_reviewed",
        "training_eligible": True,
        "source": "rule_generated",
        "split": "train",
    }


def _request() -> dict:
    return {
        "request_id": "augment-training-plan-validation",
        "primary_intent": "training_plan",
        "secondary_intents": ["nutrition_advice"],
        "language_factor": "colloquial",
        "split_target": "validation",
    }


def _teacher(message: str = "帮我排个增肌计划，练完怎么吃也说说") -> dict:
    return {
        "request_id": "augment-training-plan-validation",
        "user_message": message,
        "primary_intent": "training_plan",
        "secondary_intents": ["nutrition_advice"],
        "source": "teacher_generated",
        "generator_id": "teacher:test",
        "prompt_version": "intent-augmentation-v1",
        "human_review_status": "pending",
        "split_target": "validation",
        "language_factor": "colloquial",
    }


def test_build_dataset_accepts_contract_valid_teacher_row_with_provenance():
    rows, manifest, audit = build_dataset([_base_row()], [_request()], [_teacher()])

    teacher = rows[-1]
    assert audit["accepted_rows"] == 1
    assert manifest["teacher_rows"] == 1
    assert teacher["source"] == "teacher_generated"
    assert teacher["human_review_status"] == "pending"
    assert teacher["training_eligible"] is True
    assert teacher["teacher_model"] == "teacher:test"
    assert json.loads(teacher["assistant_response"])["secondary_intents"] == ["nutrition_advice"]


def test_build_dataset_rejects_label_mismatch_and_known_bad_request():
    mismatched = _teacher()
    mismatched["primary_intent"] = "nutrition_advice"
    rows, _, audit = build_dataset(
        [_base_row()],
        [_request()],
        [mismatched, _teacher("换个训练安排，也给点练后饮食建议")],
        excluded_request_ids={"augment-training-plan-validation"},
    )

    assert len(rows) == 1
    assert audit["accepted_rows"] == 0
    assert audit["rejected_rows"] == 2
    assert audit["rejection_reason_counts"]["curation_exclusion"] == 2
    assert audit["rejection_reason_counts"]["primary_label_mismatch"] == 1


def test_build_dataset_rejects_exact_duplicate_of_base_message():
    rows, _, audit = build_dataset([_base_row()], [_request()], [_teacher("帮我安排训练")])

    assert len(rows) == 1
    assert audit["rejection_reason_counts"] == {"exact_duplicate": 1}


def test_multi_secondary_examples_cover_two_labels_without_split_leaks():
    rows = [row.to_dict() for row in build_multi_secondary_examples()]
    validation = validate_training_rows(rows)

    assert validation["error_count"] == 0
    assert {len(json.loads(row["assistant_response"])["secondary_intents"]) for row in rows} == {2}
    assert {row["split"] for row in rows} == {"train", "validation"}
