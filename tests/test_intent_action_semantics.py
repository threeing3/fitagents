"""Domain evidence must be separate from generic write authorization."""

import pytest

from fast_api.app.services.intent_decision import IntentRouter


@pytest.mark.parametrize(
    "message",
    [
        "我刚完成30分钟哑铃训练，主观强度7分，帮我记录下来，再告诉我明天怎么安排？",
        "I did bench today, please record it.",
        "帮我记录这个信息",
        "Please record this information.",
    ],
)
def test_generic_record_request_does_not_invent_nutrition_task(message):
    decision = IntentRouter().analyze(message)
    assert "nutrition_log" not in [decision.primary_intent, *decision.secondary_intents]


@pytest.mark.parametrize(
    "message",
    ["早餐吃了鸡蛋，帮我记录", "帮我记录今天的饮食", "Record my meal: milk and eggs"],
)
def test_meal_record_keeps_nutrition_task(message):
    decision = IntentRouter().analyze(message)
    assert "nutrition_log" in [decision.primary_intent, *decision.secondary_intents]


def test_joint_workout_and_meal_record_retains_both_tasks():
    decision = IntentRouter().analyze("我刚完成30分钟哑铃训练，早餐吃了鸡蛋，帮我记录下来")
    assert {"nutrition_log", "training_log"}.issubset(
        {decision.primary_intent, *decision.secondary_intents}
    )


def test_joint_record_and_history_query_retains_both_tasks():
    decision = IntentRouter().analyze("我刚完成跑步30分钟，帮我记录，再查一下我上次跑步多久")
    assert {"training_log", "memory_query"}.issubset(
        {decision.primary_intent, *decision.secondary_intents}
    )


@pytest.mark.parametrize(
    "message",
    [
        "查一下我上次训练了多久",
        "我上次完成的训练是多少分钟？",
        "查一下我上次跑步的记录",
    ],
)
def test_historical_query_is_not_a_new_training_log(message):
    decision = IntentRouter().analyze(message)
    intents = {decision.primary_intent, *decision.secondary_intents}
    assert "memory_query" in intents
    assert "training_log" not in intents


def test_past_query_does_not_remove_current_workout_fact():
    decision = IntentRouter().analyze("我刚完成跑步30分钟，帮我记录，再查一下我上次训练了多久")
    assert {"training_log", "memory_query"}.issubset(
        {decision.primary_intent, *decision.secondary_intents}
    )


@pytest.mark.parametrize(
    "message,risk",
    [
        ("我今天抬右肩会刺痛，先别安排推举；能帮我看看本周训练要怎么调整吗？", "medium"),
        ("我现在胸口闷，还呼吸困难。今天该怎么训练？", "high"),
    ],
)
def test_training_adjustment_request_survives_other_clause_constraint(message, risk):
    decision = IntentRouter().analyze(message)
    assert {"injury_or_risk", "training_plan"}.issubset(
        {decision.primary_intent, *decision.secondary_intents}
    )
    assert decision.risk_level == risk
    assert decision.allowed_actions["generate_plan"] is False


@pytest.mark.parametrize(
    "message",
    [
        "先别安排训练计划",
        "不要生成新的训练计划",
        "我只是想了解力量训练后应该休息多久，不要给我安排训练",
    ],
)
def test_plan_negation_without_separate_request_stays_blocked(message):
    assert not IntentRouter().is_plan_request(message)


def test_exercise_sets_query_and_write_remain_distinct_tasks():
    router = IntentRouter()
    query = router.analyze("你还记得我上次深蹲做了几组吗？我只是查询，不要新增记录。")
    assert "memory_query" in {query.primary_intent, *query.secondary_intents}
    assert "training_log" not in {query.primary_intent, *query.secondary_intents}
    both = router.analyze("昨晚深蹲做完三组，请记录；顺便告诉我上次深蹲做了几组。")
    assert {"training_log", "memory_query"}.issubset({both.primary_intent, *both.secondary_intents})


@pytest.mark.parametrize(
    "message,required,forbidden,risk,plan_allowed",
    [
        (
            "室友现在头晕，但我没有任何不适。请帮我制定今天的训练计划。",
            "training_plan",
            "injury_or_risk",
            "low",
            True,
        ),
        (
            "请查一下我上次卧推用了多少公斤；这次不要新增训练记录。",
            "memory_query",
            "training_log",
            "low",
            False,
        ),
        (
            "今天深蹲做了五组，请记下来；再告诉我上次做了几组。",
            "memory_query",
            "general_chat",
            "low",
            False,
        ),
        (
            "档案里把我的目标写成了减脂，这是错的；请改为增肌。",
            "profile_correction",
            "general_chat",
            "low",
            False,
        ),
    ],
)
def test_second_probe_failures_have_semantic_routes(
    message, required, forbidden, risk, plan_allowed
):
    decision = IntentRouter().analyze(message)
    intents = {decision.primary_intent, *decision.secondary_intents}
    assert required in intents
    assert forbidden not in intents
    assert decision.risk_level == risk
    assert decision.allowed_actions["generate_plan"] is plan_allowed


def test_elliptical_sets_query_and_write_keep_both_tasks():
    decision = IntentRouter().analyze("今天深蹲做了五组，请记下来；再告诉我上次做了几组。")
    assert {"training_log", "memory_query"}.issubset(
        {decision.primary_intent, *decision.secondary_intents}
    )


def test_other_person_knee_pain_does_not_block_self_plan():
    router = IntentRouter()
    other = router.analyze("妹妹今天膝盖疼，我本人没受伤。请给我制定本周训练计划。")
    own = router.analyze("我今天膝盖疼，妹妹没受伤。请给我制定本周训练计划。")
    assert "injury_or_risk" not in {other.primary_intent, *other.secondary_intents}
    assert other.risk_level == "low"
    assert other.allowed_actions["generate_plan"] is True
    assert "injury_or_risk" in {own.primary_intent, *own.secondary_intents}
    assert own.risk_level == "medium"
    assert own.allowed_actions["generate_plan"] is False


@pytest.mark.parametrize(
    "message",
    [
        "今天早餐该怎么吃？不要帮我记录饮食。",
        "今天午餐吃什么合适，不用记录。",
        "What should I eat for breakfast? Do not record a meal.",
    ],
)
def test_food_advice_with_negated_write_never_requests_nutrition_log(message):
    decision = IntentRouter().analyze(message)
    intents = {decision.primary_intent, *decision.secondary_intents}
    assert "nutrition_advice" in intents
    assert "nutrition_log" not in intents
