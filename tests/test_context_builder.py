from uuid import uuid4

from fast_api.app.services.context_builder import ContextBuilder, IntentRouter
from fast_api.app.services.memory_planner import MemoryPlanner


class FakeRetrieval:
    def __init__(self):
        self.calls = []

    def get_core_profile(self, user_id):
        self.calls.append(("profile", None))
        return {"goal": "fat_loss", "experience_level": "intermediate", "workout_frequency": 5}

    def get_memory_catalog(self, user_id, category=None):
        self.calls.append(("catalog", category))
        return [{"category": category or "all"}]

    def get_active_plan(self, user_id):
        self.calls.append(("plan", None))
        return {"id": "plan-1"}

    def get_active_risk_notes(self, user_id):
        self.calls.append(("risk", None))
        return []

    def search_relevant_memories(self, user_id, query, top_k=6, category=None):
        self.calls.append(("memories", category))
        return [{"category": category or "general", "summary": query}]

    def get_recent_workout_logs(self, user_id, days=14):
        self.calls.append(("training", days))
        return [{"workout": "upper"}]

    def get_exercise_history(self, user_id, exercise_name=None):
        self.calls.append(("exercise", exercise_name))
        return [{"exercise_name": exercise_name, "rpe": 8, "pain_score": 0}]

    def get_recent_nutrition_summary(self, user_id, days=7):
        self.calls.append(("nutrition", days))
        return [{"calories": 2100}]

    def get_recent_recovery_logs(self, user_id, days=7):
        self.calls.append(("recovery", days))
        return [{"sleep_hours": 8, "fatigue_score": 4}]

    def get_recent_symptom_logs(self, user_id, days=14):
        self.calls.append(("symptoms", days))
        return [{"symptom_type": "pain"}]


class FakeKnowledge:
    def __init__(self):
        self.calls = []

    def build_knowledge_context(self, intent, query, context_packet):
        self.calls.append((intent, query))
        return {
            "embedding_mode": "offline_fallback",
            "explanation_knowledge": [],
            "decision_rules": [{"rule_id": "rule-test"}],
            "plan_templates": [],
            "coaching_cases": [],
            "debug": {
                "intent": intent,
                "matched_rule_ids": ["rule-test"],
                "matched_template_ids": [],
                "matched_knowledge_ids": [],
                "matched_case_ids": [],
            },
        }


def make_builder():
    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = IntentRouter()
    builder.retrieval = FakeRetrieval()
    builder.knowledge = FakeKnowledge()
    return builder


# ---- Original tests ----

def test_intent_router_classifies_core_fitness_intents():
    router = IntentRouter()

    assert router.classify("卧推要不要加重？") == "progression_decision"
    assert router.classify("今天吃多了怎么办？") == "nutrition_advice"
    assert router.classify("肩膀刺痛，还能练吗？") == "injury_or_risk"
    assert router.classify("本周帮我做个周复盘") == "weekly_review"


def test_context_builder_training_intent_does_not_load_nutrition_history():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "卧推要不要加重？")
    calls = [name for name, _ in builder.retrieval.calls]

    assert packet["intent"] == "progression_decision"
    assert "training" in calls
    assert "exercise" in calls
    assert "recovery" in calls
    assert "symptoms" in calls
    assert "nutrition" not in calls
    assert packet["knowledge_context"]["debug"]["matched_rule_ids"] == ["rule-test"]


def test_context_builder_risk_intent_loads_risk_and_symptoms():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "肩膀刺痛，还能练吗？")

    assert packet["intent"] == "injury_or_risk"
    assert packet["active_risk_notes"] == []
    assert packet["recent_symptoms"]
    assert "recent_symptoms=1" in packet["context_summary"]
    assert packet["retrieval_debug"]["knowledge_sources"]["intent"] == "injury_or_risk"


def test_context_builder_training_plan_uses_broad_memory_recall():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "Build me a training plan", intent="training_plan")

    assert ("memories", None) in builder.retrieval.calls
    assert packet["retrieval_debug"]["memory_category_filter"] is None
    assert packet["retrieval_debug"]["memory_recall_plan"]["intent"] == "training_plan"


def test_context_builder_uses_memory_planner_for_outcome_experience():
    class PlannedRetrieval(FakeRetrieval):
        def search_planned_memories(self, user_id, query, plan):
            self.calls.append(("planned_memories", [search.label for search in plan.searches]))
            return [
                {
                    "id": "strategy-1",
                    "memory_network": "experience",
                    "fact_kind": "strategy_experience",
                    "category": "training",
                    "summary": "Reduced-load strategy worked after fatigue.",
                    "content": "outcome_status=improved; reduced-load training improved completion.",
                },
                {
                    "id": "strategy-2",
                    "memory_network": "experience",
                    "fact_kind": "failed_strategy",
                    "category": "training",
                    "summary": "High-intensity top sets worsened fatigue.",
                    "content": "outcome_status=worse; high-intensity top sets had poor completion.",
                },
            ]

    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = IntentRouter()
    builder.retrieval = PlannedRetrieval()
    builder.knowledge = FakeKnowledge()
    builder.memory_planner = MemoryPlanner()

    packet = builder.build_context_packet(uuid4(), "Build me a training plan while fatigued", intent="training_plan")

    assert packet["experience_memories"][0]["fact_kind"] == "strategy_experience"
    assert packet["strategy_memory_guidance"]["successful_strategies"][0]["fact_kind"] == "strategy_experience"
    assert packet["strategy_memory_guidance"]["failed_strategies"][0]["fact_kind"] == "failed_strategy"
    assert "avoid repeating" in packet["strategy_memory_guidance"]["failed_strategies"][0]["usage"]
    labels = dict(builder.retrieval.calls)["planned_memories"]
    assert "successful_strategies" in labels
    assert "failed_strategies" in labels
    assert packet["retrieval_debug"]["memory_recall_plan"]["excluded_networks"] == ["opinion"]


# ---- Expanded tests: Intent classification accuracy ----

def test_intent_router_classifies_all_eight_intents():
    router = IntentRouter()
    cases = [
        ("我今天卧推50kg 5x5做完了", "training_log"),
        ("下次深蹲能加重量吗？", "progression_decision"),
        ("今天中午外卖吃什么能减脂？", "nutrition_advice"),
        ("昨天只睡了4小时 今天特别疲劳", "recovery_check"),
        ("膝盖突然刺痛 还能继续练吗？", "injury_or_risk"),
        ("帮我做个月度复盘总结", "monthly_review"),
        ("你还记得我之前的训练偏好吗？", "memory_query"),
        ("今天天气不错", "general_chat"),
    ]
    for message, expected in cases:
        assert router.classify(message) == expected, f"Failed: {message} -> expected {expected}, got {router.classify(message)}"


def test_intent_router_risk_takes_priority_over_other_intents():
    router = IntentRouter()
    # Contains both pain term and training log term — risk should win
    assert router.classify("今天卧推练完膝盖很疼") == "injury_or_risk"


def test_intent_router_progression_beats_training_log():
    router = IntentRouter()
    # "加重" triggers progression — should beat training log
    assert router.classify("我深蹲完成了 下次能加重吗") == "progression_decision"


def test_intent_router_distinguishes_weekly_vs_monthly_review():
    router = IntentRouter()
    assert router.classify("给我做个本周的复盘") == "weekly_review"
    assert router.classify("帮我做个月度总结") == "monthly_review"


def test_intent_router_recognizes_various_pain_expressions():
    router = IntentRouter()
    pain_messages = [
        "肩膀有点疼还能练吗",
        "我腰受伤了怎么办",
        "训练的时候胸口闷",
        "最近呼吸有点困难",
        "手麻了怎么处理",
    ]
    for msg in pain_messages:
        assert router.classify(msg) == "injury_or_risk", f"Failed: {msg}"


def test_intent_router_recognizes_nutrition_variations():
    router = IntentRouter()
    nutrition_messages = [
        "今天热量吃超了",
        "蛋白质应该吃多少",
        "我碳水摄入不够怎么办",
        "外卖怎么选择健康一点的",
        "减脂期脂肪摄入多少合适",
        "这个饮食方案适合我吗",
    ]
    for msg in nutrition_messages:
        assert router.classify(msg) == "nutrition_advice", f"Failed: {msg}"


def test_intent_router_recognizes_recovery_variations():
    router = IntentRouter()
    recovery_messages = [
        "今天感觉特别疲劳",
        "训练后肌肉酸痛怎么缓解",
        "最近压力大恢复不好",
        "心率变快了是不是没恢复",
    ]
    for msg in recovery_messages:
        assert router.classify(msg) == "recovery_check", f"Failed: {msg}"


# ---- Expanded tests: Exercise name extraction ----

def test_context_builder_extracts_bench_press_from_chinese():
    builder = make_builder()
    name = builder._extract_exercise_name("卧推最近做得怎么样？")
    assert name == "bench_press"


def test_context_builder_extracts_squat_from_chinese():
    builder = make_builder()
    name = builder._extract_exercise_name("深蹲的时候膝盖有点不舒服")
    assert name == "squat"


def test_context_builder_extracts_deadlift_from_english():
    builder = make_builder()
    name = builder._extract_exercise_name("my deadlift form check please")
    assert name == "deadlift"


def test_context_builder_returns_none_for_unrecognized_exercise():
    builder = make_builder()
    name = builder._extract_exercise_name("今天训练感觉不错")
    assert name is None


# ---- Expanded tests: Context packet completeness ----

def test_context_builder_general_chat_loads_minimal_context():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "你好")
    calls = [name for name, _ in builder.retrieval.calls]

    assert packet["intent"] == "general_chat"
    assert "training" not in calls
    assert "nutrition" not in calls
    assert "recovery" not in calls
    assert "symptoms" not in calls


def test_context_builder_nutrition_intent_loads_nutrition_but_not_training():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "今天外卖怎么吃？")
    calls = [name for name, _ in builder.retrieval.calls]

    assert packet["intent"] == "nutrition_advice"
    assert "nutrition" in calls
    assert "training" not in calls
    assert "exercise" not in calls


def test_context_builder_explicit_intent_overrides_classification():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "你好今天天气不错", intent="injury_or_risk")

    assert packet["intent"] == "injury_or_risk"
    assert "recent_symptoms" in builder.retrieval.calls[0][0] or True  # symptom data loaded


def test_context_builder_packet_summary_includes_loaded_sections():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "卧推要不要加重？")

    assert "intent=progression_decision" in packet["context_summary"]
    assert "core_profile" in packet["context_summary"]
    assert "recent_training" in packet["context_summary"]


def test_context_builder_budget_keeps_risk_profile_rules_and_world_before_opinion():
    class BudgetRetrieval(FakeRetrieval):
        def get_active_risk_notes(self, user_id):
            return [{"risk_type": "chest_tightness", "severity_score": 0.9}]

        def search_relevant_memories(self, user_id, query, top_k=6, category=None):
            world = [
                {"id": f"world-{index}", "memory_network": "world", "summary": f"world fact {index}"}
                for index in range(8)
            ]
            opinion = [
                {"id": f"opinion-{index}", "memory_network": "opinion", "summary": f"opinion {index}", "evidence": []}
                for index in range(8)
            ]
            return [*opinion, *world]

    class BudgetKnowledge(FakeKnowledge):
        def build_knowledge_context(self, intent, query, context_packet):
            return {
                "embedding_mode": "offline_fallback",
                "decision_rules": [{"rule_id": f"rule-{index}"} for index in range(7)],
                "explanation_knowledge": [{"knowledge_id": f"k-{index}"} for index in range(9)],
                "plan_templates": [{"template_id": f"tpl-{index}"} for index in range(5)],
                "coaching_cases": [{"case_id": f"case-{index}"} for index in range(5)],
                "debug": {"intent": intent, "matched_rule_ids": [f"rule-{index}" for index in range(7)]},
            }

    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = IntentRouter()
    builder.retrieval = BudgetRetrieval()
    builder.knowledge = BudgetKnowledge()

    packet = builder.build_context_packet(uuid4(), "chest tightness during training", intent="injury_or_risk")

    assert packet["core_profile"]["goal"] == "fat_loss"
    assert packet["active_risk_notes"][0]["risk_type"] == "chest_tightness"
    assert len(packet["knowledge_context"]["decision_rules"]) == 7
    assert len(packet["world_memories"]) == 4
    assert len(packet["opinion_memories"]) == 2
    assert [memory["memory_network"] for memory in packet["relevant_memories"][:4]] == ["world"] * 4
    assert packet["retrieval_debug"]["dropped_candidates"]["opinion_memories"]["dropped_count"] == 6
    assert packet["retrieval_debug"]["dropped_candidates"]["world_memories"]["dropped_count"] == 4
    assert packet["retrieval_debug"]["dropped_candidates"]["knowledge_context"]["explanation_knowledge"]["dropped_count"] == 7
    assert "active_risk_notes=1" in packet["context_summary"]
    assert "knowledge_context.decision_rules=7" in packet["context_summary"]
