from algorithm.business.acceptance_model import LogisticAcceptanceModel, MajorityAcceptanceModel, fit_acceptance_model
from algorithm.business.business_baseline import run_business_baseline
from algorithm.business.feature_builder import build_features
from algorithm.evaluation.business_eval import mean_ndcg, probabilistic_metrics, roc_auc


def test_outcome_fields_are_excluded_from_default_features():
    example = {"user_message": "plan", "outcome": {"negative_feedback": True}, "quality_labels": {"overall_score": 0.8}}
    features = build_features(example)
    assert "negative_feedback_leakage_probe" not in features
    assert build_features(example, include_outcome_features=True)["negative_feedback_leakage_probe"] == 1.0


def test_dependency_free_logistic_model_exposes_dictionary_row_api():
    rows = [{"signal": 0.0}, {"signal": 1.0}, {"signal": 0.1}, {"signal": 0.9}] * 3
    labels = [0, 1, 0, 1] * 3
    model = LogisticAcceptanceModel().fit(rows, labels, epochs=100)
    probabilities = model.predict_proba([{"signal": 0.0}, {"signal": 1.0}])
    assert probabilities[0] < probabilities[1]
    assert isinstance(fit_acceptance_model(rows, labels).predict(rows), list)
    assert isinstance(MajorityAcceptanceModel().fit(rows, labels).predict(rows), list)


def test_business_metrics_and_simulated_baseline_are_reportable():
    assert roc_auc([0, 1], [0.1, 0.9]) == 1.0
    metrics = probabilistic_metrics([0, 1], [0.1, 0.9])
    assert metrics["auroc"] == 1.0
    assert mean_ndcg([{"relevances": [1, 0]}], 5) == 1.0
    report = run_business_baseline(count=120, seed=3)
    assert report["dataset"]["source_counts"] == {"synthetic": 120}
    assert report["dataset"]["label_source_counts"] == {"simulated_outcome": 120}
    assert report["acceptance_model"]["metrics"]["support"] > 0
    assert 0.0 <= report["ranking"]["mean_ndcg_at_5"] <= 1.0
