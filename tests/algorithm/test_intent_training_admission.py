import json

from algorithm.evaluation.intent_training_admission import audit_rows, base_family


def _row(identifier, message, split, family, review="pending", group=None):
    row = {
        "example_id": identifier,
        "user_message": message,
        "assistant_response": json.dumps(
            {"primary_intent": "training_plan", "secondary_intents": []}
        ),
        "intent_label": "training_plan",
        "source": "teacher_generated",
        "split": split,
        "template_family": family,
        "human_review_status": review,
        "training_eligible": True,
    }
    if group is not None:
        row["scenario_group_id"] = group
    return row


def test_split_marker_does_not_masquerade_as_family_isolation():
    assert base_family("primary_training_plan_train_0") == "primary_training_plan"
    assert base_family("primary_training_plan_validation") == "primary_training_plan"
    rows = [
        _row("a", "请安排今天训练", "train", "primary_training_plan_train_0"),
        _row("b", "给我今天的训练安排", "validation", "primary_training_plan_validation"),
    ]
    report = audit_rows(rows)
    assert report["cross_split_exact_message_groups"] == 0
    assert report["shared_base_template_families"] == 1
    assert report["scenario_split_verified"] is False
    assert report["quality_training_admitted"] is False


def test_pending_eligible_row_is_not_semantically_approved():
    report = audit_rows([_row("a", "早餐，两个鸡蛋。", "train", "nutrition_train", group="g1")])
    assert report["structural_checks_pass"] is True
    assert report["eligible_review_counts"] == {"pending": 1}
    assert report["human_review_complete_for_train"] is False
    assert report["human_review_complete_for_validation"] is False
    assert report["quality_training_admitted"] is False


def test_duplicate_and_cross_split_text_are_separate_findings():
    rows = [
        _row("a", "帮我安排训练", "train", "plan_train", "approved", "g1"),
        _row("b", "帮我安排训练", "validation", "plan_validation", "approved", "g2"),
    ]
    report = audit_rows(rows)
    assert report["duplicate_message_groups"] == 1
    assert report["rows_in_duplicate_message_groups"] == 2
    assert report["cross_split_exact_message_groups"] == 1
    assert report["structural_checks_pass"] is False
    assert report["quality_training_admitted"] is False


def test_reviewed_grouped_data_can_pass_audit_without_semantic_claim():
    rows = [
        _row("a", "请安排今天训练", "train", "plan_train", "approved", "g1"),
        _row("b", "今天练什么", "validation", "plan_validation", "approved", "g2"),
    ]
    report = audit_rows(rows)
    assert report["scenario_split_verified"] is True
    assert report["quality_training_admitted"] is True
    assert report["eligible_example_ids_by_split"] == {"train": ["a"], "validation": ["b"]}
    assert "do not validate label semantics" in report["interpretation"].lower()


def test_unreviewed_validation_cannot_pass_quality_admission():
    rows = [
        _row("a", "请安排今天训练", "train", "plan_train", "approved", "g1"),
        _row("b", "今天练什么", "validation", "plan_validation", "pending", "g2"),
    ]
    report = audit_rows(rows)
    assert report["human_review_complete_for_train"] is True
    assert report["human_review_complete_for_validation"] is False
    assert report["quality_training_admitted"] is False
