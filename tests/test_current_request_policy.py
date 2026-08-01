from uuid import uuid4

from fast_api.app.services.context_builder import ContextBuilder, IntentRouter


class FakeRetrieval:
    def get_core_profile(self, user_id):
        return {
            "age": 28,
            "height_cm": 175,
            "weight_kg": 76,
            "goal": "fat_loss",
            "experience_level": "intermediate",
            "equipment_available": ["gym"],
            "workout_frequency": 5,
        }

    def get_memory_catalog(self, user_id, category=None):
        return []

    def get_active_plan(self, user_id):
        return {"id": "plan-1"}

    def get_active_risk_notes(self, user_id):
        return []

    def search_relevant_memories(self, user_id, query, top_k=6, category=None):
        return []

    def get_recent_workout_logs(self, user_id, days=14):
        return []

    def get_exercise_history(self, user_id, exercise_name=None):
        return []

    def get_recent_nutrition_summary(self, user_id, days=7):
        return []

    def get_recent_recovery_logs(self, user_id, days=7):
        return []

    def get_recent_symptom_logs(self, user_id, days=14):
        return []


class FakeKnowledge:
    def build_knowledge_context(self, intent, query, context_packet):
        return {
            "embedding_mode": "offline_fallback",
            "explanation_knowledge": [],
            "decision_rules": [],
            "plan_templates": [],
            "coaching_cases": [],
            "debug": {"intent": intent, "matched_template_ids": []},
        }


def make_builder():
    builder = ContextBuilder.__new__(ContextBuilder)
    builder.intent_router = IntentRouter()
    builder.retrieval = FakeRetrieval()
    builder.knowledge = FakeKnowledge()
    return builder


def test_general_question_does_not_inherit_prior_plan_request():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "这个问题为什么又带上了计划？")

    assert packet["intent"] == "general_chat"
    assert packet["active_plan"] is None
    assert packet["current_request_policy"]["should_generate_plan"] is False
    assert packet["current_request_policy"]["allow_plan_content"] is False


def test_explicit_plan_question_allows_plan_generation_and_plan_context():
    builder = make_builder()

    packet = builder.build_context_packet(uuid4(), "今天应该练什么？")

    assert packet["intent"] == "training_plan"
    assert packet["active_plan"] == {"id": "plan-1"}
    assert packet["current_request_policy"]["should_generate_plan"] is True
    assert packet["current_request_policy"]["allow_plan_content"] is True


def test_unrelated_questions_stay_general_chat_even_if_they_mention_plan_as_a_bug():
    router = IntentRouter()

    assert router.classify("刚才为什么你又带上了计划？") == "general_chat"
    assert router.classify("这个项目的日志是怎么记录的？") == "general_chat"


def test_negated_plan_mentions_do_not_trigger_plan_generation():
    router = IntentRouter()

    assert router.classify("只解释原因，不要给我训练计划。") == "general_chat"
    assert router.classify("不用生成计划，先回答这个问题。") == "general_chat"
