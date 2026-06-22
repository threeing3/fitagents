from fast_api.app.services.runtime_router import RuntimeRouter


def route_mode(message: str) -> str:
    return RuntimeRouter().route(message).mode


def test_concept_explanation_uses_llm_driven():
    route = RuntimeRouter().route("What is progressive overload?")

    assert route.mode == "llm_driven"
    assert route.confidence > 0.7
    assert any(rule.startswith("explanation:") for rule in route.matched_rules)


def test_nutrition_concept_without_logging_uses_llm_driven():
    route = RuntimeRouter().route("Explain the difference between protein and carbs")

    assert route.mode == "llm_driven"


def test_training_plan_uses_code_driven_with_intent_decision():
    route = RuntimeRouter().route("Please create a one week training plan")

    assert route.mode == "code_driven"
    assert "intent:training_plan" in route.matched_rules
    assert route.intent_decision["primary_intent"] == "training_plan"


def test_food_log_uses_code_driven_with_intent_decision():
    route = RuntimeRouter().route("I ate eggs and rice for breakfast, please record my meal")

    assert route.mode == "code_driven"
    assert "intent:nutrition_log" in route.matched_rules
    assert route.intent_decision["primary_intent"] == "nutrition_log"


def test_health_risk_uses_code_driven_with_intent_decision():
    route = RuntimeRouter().route("I have chest tightness, can I train today?")

    assert route.mode == "code_driven"
    assert "intent:injury_or_risk" in route.matched_rules
    assert route.intent_decision["risk_level"] == "high"


def test_plan_edit_uses_code_driven():
    route = RuntimeRouter().route("Change my plan to four training days per week")

    assert route.mode == "code_driven"
    assert any(rule.startswith("plan_edit:") for rule in route.matched_rules)


def test_greeting_uses_llm_driven():
    route = RuntimeRouter().route("hello")

    assert route.mode == "llm_driven"
    assert any(rule.startswith("chat:") for rule in route.matched_rules)


def test_training_log_uses_code_driven_with_intent_decision():
    route = RuntimeRouter().route("I trained chest today and did bench 60kg")

    assert route.mode == "code_driven"
    assert "intent:training_log" in route.matched_rules
    assert route.intent_decision["entities"]["weight_kg"] == 60


def test_unknown_defaults_to_code_driven():
    route = RuntimeRouter().route("What do you think about this situation?")

    assert route.mode == "code_driven"
    assert route.matched_rules == ["fallback.unknown_defaults_to_code_driven"]
