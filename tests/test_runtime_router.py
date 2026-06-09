from fast_api.app.services.runtime_router import RuntimeRouter


def route_mode(message: str) -> str:
    return RuntimeRouter().route(message).mode


def test_concept_explanation_uses_llm_driven():
    route = RuntimeRouter().route("什么是渐进超负荷？")

    assert route.mode == "llm_driven"
    assert route.confidence > 0.7
    assert any(rule.startswith("explanation:") for rule in route.matched_rules)


def test_nutrition_concept_without_logging_uses_llm_driven():
    route = RuntimeRouter().route("帮我解释一下蛋白质和碳水的区别")

    assert route.mode == "llm_driven"


def test_training_plan_uses_code_driven():
    route = RuntimeRouter().route("帮我制定一周训练计划")

    assert route.mode == "code_driven"
    assert any(rule.startswith("training_plan:") for rule in route.matched_rules)


def test_food_log_uses_code_driven():
    route = RuntimeRouter().route("我今天早餐吃了两个鸡蛋和一碗米饭，帮我记录")

    assert route.mode == "code_driven"
    assert any(rule.startswith("nutrition_record:") for rule in route.matched_rules)


def test_health_risk_uses_code_driven():
    route = RuntimeRouter().route("我胸口有点闷，今天还能练吗")

    assert route.mode == "code_driven"
    assert any(rule.startswith("risk:") for rule in route.matched_rules)


def test_plan_edit_uses_code_driven():
    route = RuntimeRouter().route("把我的计划改成一周四练")

    assert route.mode == "code_driven"
    assert any(rule.startswith("plan_edit:") for rule in route.matched_rules)


def test_greeting_uses_llm_driven():
    route = RuntimeRouter().route("你好")

    assert route.mode == "llm_driven"
    assert any(rule.startswith("chat:") for rule in route.matched_rules)


def test_training_log_uses_code_driven():
    route = RuntimeRouter().route("我今天练胸了")

    assert route.mode == "code_driven"
    assert any(rule.startswith("training_") for rule in route.matched_rules)


def test_unknown_defaults_to_code_driven():
    route = RuntimeRouter().route("这个情况你怎么看")

    assert route.mode == "code_driven"
    assert route.matched_rules == ["fallback.unknown_defaults_to_code_driven"]
