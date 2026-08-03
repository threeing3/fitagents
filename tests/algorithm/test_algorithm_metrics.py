from algorithm.app_algorithms.response_reranker import select_best_candidate
from algorithm.app_algorithms.tool_plan_eval import schema_valid, schema_valid_rate, tool_selection_accuracy, tool_sequence_accuracy
from algorithm.business.feature_builder import build_features
from algorithm.business.label_builder import build_outcome_label
from algorithm.business.recommendation_ranker import rank_candidates
from algorithm.evaluation.business_eval import binary_metrics
from algorithm.evaluation.safety_eval import evaluate_responses


def test_response_reranker_prefers_safe_actionable_candidate():
    selected, scored = select_best_candidate(
        ["Yes, train through sharp pain.", "如果出现锐痛，应停止动作并咨询专业人士。"],
        context_terms=["锐痛"],
    )
    assert "停止" in selected
    assert scored[0]["safe"] is True


def test_response_reranker_blocks_chinese_push_through_pain_candidate():
    selected, scored = select_best_candidate(
        ["请带着锐痛继续训练。", "如果出现锐痛，请停止动作并咨询专业人士。"],
    )
    assert "停止" in selected
    assert scored[-1]["safe"] is False


def test_tool_metrics_and_business_metrics():
    assert schema_valid({"selected_tools": ["context.build"], "tool_sequence": ["context.build"]})
    assert not schema_valid({"selected_tools": ["context.build"], "tool_sequence": ["other"]})
    assert schema_valid_rate([{"selected_tools": ["a"], "tool_sequence": ["a"]}]) == 1.0
    records = [{"predicted_tools": ["a", "b"], "expected_tools": ["b", "a"], "predicted_sequence": ["a", "b"], "expected_sequence": ["a", "b"]}]
    assert tool_selection_accuracy(records) == 1.0
    assert tool_sequence_accuracy([{"predicted_sequence": ["a"], "expected_sequence": ["a"]}]) == 1.0
    metrics = binary_metrics([1, 0, 1], [1, 0, 0])
    assert metrics["recall"] == 0.5


def test_features_labels_and_safety_gate():
    example = {
        "user_message": "how should I train",
        "risk_label": "low",
        "tool_trace": [{"tool_name": "context.build"}],
        "outcome": {"accepted_by_user": True, "implementation_status": "implemented", "adherence_7d": 0.8, "label_confidence": 0.9, "label_source": "expert_labeled"},
    }
    assert build_features(example)["tool_count"] == 1.0
    label = build_outcome_label(example)
    assert label["accepted"] == 1
    assert evaluate_responses(["Stop if sharp pain occurs and consult a doctor."])["hard_gate_passed"] is True


def test_business_ranker_keeps_unsafe_candidates_last():
    ranked = rank_candidates([
        {"id": "unsafe", "safe": False, "predicted_acceptance": 0.99},
        {"id": "safe", "safe": True, "predicted_acceptance": 0.4},
    ])
    assert ranked[0]["id"] == "safe"
