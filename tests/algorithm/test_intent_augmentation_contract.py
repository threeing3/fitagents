from algorithm.data.intent_augmentation_contract import (
    IntentAugmentationOutput,
    IntentAugmentationRequest,
)
from algorithm.datasets.build_intent_augmentation_requests import (
    build_manifest,
    build_requests,
)
from algorithm.evaluation.intent_diversity_audit import audit_diversity
from algorithm.inference.intent_catalog import AgentIntentCatalog


def test_requests_cover_every_primary_and_target_both_splits_without_eval_access():
    rows = build_requests()

    assert {row["primary_intent"] for row in rows} >= AgentIntentCatalog.VALID_INTENTS
    assert {row["split_target"] for row in rows} == {"train", "validation"}
    assert all(not row["development_text_access"] for row in rows)
    assert all(not row["fixed_test_text_access"] for row in rows)
    assert build_manifest(rows)["request_count"] == 48


def test_request_rejects_evaluation_text_access():
    request = IntentAugmentationRequest(
        request_id="unsafe",
        primary_intent="training_plan",
        secondary_intents=(),
        semantic_brief="安排训练",
        language_factor="colloquial",
        requested_source="teacher_generated",
        split_target="train",
        development_text_access=True,
    )

    assert "evaluation text access is forbidden during augmentation" in request.validate()


def test_teacher_output_requires_generator_and_review_state():
    output = IntentAugmentationOutput(
        request_id="r1",
        user_message="帮我排一下明天练啥",
        primary_intent="training_plan",
        secondary_intents=(),
        source="teacher_generated",
        generator_id="",
        prompt_version="v1",
    )

    assert "teacher_generated output requires generator_id" in output.validate()


def test_diversity_audit_exposes_duplicates_and_cross_split_similarity():
    rows = [
        {"user_message": "帮我安排训练", "split": "train", "template_family": "a"},
        {"user_message": "帮我安排训练", "split": "train", "template_family": "a"},
        {"user_message": "请帮我安排训练", "split": "validation", "template_family": "b"},
    ]

    report = audit_diversity(rows)

    assert report["exact_duplicate_rate"] > 0
    assert report["max_cross_split_trigram_jaccard"] > 0
    assert report["claims"]["semantic_diversity_proven"] is False
