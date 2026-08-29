import json

from algorithm.datasets.generate_intent_augmentations import (
    build_prompt,
    generate_outputs,
    parse_teacher_messages,
)


def _request():
    return {
        "request_id": "augment-training-plan",
        "primary_intent": "training_plan",
        "secondary_intents": ["nutrition_advice"],
        "semantic_brief": "安排增肌训练和训练后饮食",
        "language_factor": "colloquial",
        "split_target": "train",
        "prompt_version": "intent-augmentation-v1",
    }


def test_prompt_contains_semantics_but_no_evaluation_content():
    prompt = build_prompt(_request(), 2)

    assert "安排增肌训练和训练后饮食" in prompt
    assert "不得引用任何评测集" in prompt
    assert "恰好 2 条" in prompt


def test_parser_accepts_fenced_strict_json():
    result = parse_teacher_messages('```json\n{"messages":["表达一","表达二"]}\n```', 2)

    assert result == ["表达一", "表达二"]


def test_generation_preserves_labels_provenance_and_pending_review():
    outputs, failures = generate_outputs(
        [_request()],
        invoke=lambda _: json.dumps({"messages": ["练啥配什么吃", "训练饮食一起安排"]}),
        generator_id="deepseek:test",
        variants=2,
    )

    assert failures == []
    assert len(outputs) == 2
    assert outputs[0]["primary_intent"] == "training_plan"
    assert outputs[0]["secondary_intents"] == ("nutrition_advice",)
    assert outputs[0]["source"] == "teacher_generated"
    assert outputs[0]["human_review_status"] == "pending"


def test_generation_isolates_invalid_teacher_response():
    outputs, failures = generate_outputs(
        [_request()], invoke=lambda _: "not json", generator_id="deepseek:test", variants=2
    )

    assert outputs == []
    assert failures[0]["request_id"] == "augment-training-plan"
    assert failures[0]["raw_response_excerpt"] == "not json"
