from algorithm.inference.verify_service import evaluate_checks


def test_deployment_checks_require_auth_schema_and_risk_floor():
    checks = evaluate_checks(
        200,
        200,
        401,
        {
            "schema_version": "intent_decision_v2",
            "decision": {"primary_intent": "training_plan"},
        },
        {
            "schema_version": "intent_decision_v2",
            "decision": {"primary_intent": "injury_or_risk", "risk_level": "high"},
        },
    )

    assert all(checks.values())


def test_deployment_checks_reject_demoted_risk_output():
    checks = evaluate_checks(
        200,
        200,
        401,
        {
            "schema_version": "intent_decision_v2",
            "decision": {"primary_intent": "training_plan"},
        },
        {
            "schema_version": "intent_decision_v2",
            "decision": {"primary_intent": "training_plan", "risk_level": "low"},
        },
    )

    assert checks["risk_schema_valid"] is False
