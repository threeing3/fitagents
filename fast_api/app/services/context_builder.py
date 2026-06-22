import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService
from fast_api.app.services.intent_decision import IntentDecision, IntentRouter as StructuredIntentRouter
from fast_api.app.services.memory_planner import MemoryPlanner, MemoryRecallPlan
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider


class IntentRouter:
    """Rule-first intent classifier. Current user message always wins over older chat history."""

    PLAN_REQUEST_TERMS = [
        "训练计划",
        "健身计划",
        "生成计划",
        "制定计划",
        "做个计划",
        "出个计划",
        "给我计划",
        "帮我计划",
        "安排训练",
        "安排一下",
        "今天练什么",
        "今天应该练什么",
        "今天应该干什么",
        "今天干什么",
        "今日训练",
        "练什么",
        "workout plan",
        "training plan",
        "what should i do today",
        "what should i train",
    ]
    TRAINING_LOG_TERMS = ["完成", "做完", "练了", "kg", "公斤", "rpe", "组", "次数", "bench", "squat", "deadlift"]
    PROGRESSION_TERMS = ["加重", "重量", "进步", "下次", "progress", "increase", "deload", "降载"]
    NUTRITION_TERMS = ["吃", "热量", "蛋白", "碳水", "脂肪", "外卖", "外食", "calorie", "protein", "diet"]
    RECOVERY_TERMS = ["睡", "疲劳", "酸痛", "恢复", "压力", "心率", "recovery", "sleep", "tired"]
    RISK_TERMS = ["疼", "痛", "刺痛", "胸闷", "头晕", "呼吸困难", "麻木", "受伤", "甲亢", "甲状腺", "吃药", "服药", "用药", "injury", "pain", "dizzy"]
    REVIEW_TERMS = ["周复盘", "本周", "weekly", "月复盘", "monthly", "总结"]
    MEMORY_TERMS = ["你记得", "我的档案", "记忆", "memory", "profile"]

    def classify(self, message: str) -> str:
        lowered = message.lower()
        normalized = self._classify_normalized_chinese(lowered)
        if normalized:
            return normalized
        if self._contains(lowered, self.RISK_TERMS):
            return "injury_or_risk"
        if self._contains(lowered, self.REVIEW_TERMS):
            return "monthly_review" if "月" in lowered or "monthly" in lowered else "weekly_review"
        if self.is_plan_request(message):
            return "training_plan"
        if self._contains(lowered, self.PROGRESSION_TERMS):
            return "progression_decision"
        if self._contains(lowered, self.TRAINING_LOG_TERMS):
            return "training_log"
        if self._contains(lowered, self.NUTRITION_TERMS):
            return "nutrition_advice"
        if self._contains(lowered, self.RECOVERY_TERMS):
            return "recovery_check"
        if self._contains(lowered, self.MEMORY_TERMS):
            return "memory_query"
        return "general_chat"

    def _contains(self, lowered: str, terms: list[str]) -> bool:
        return any(term in lowered for term in terms)

    def _classify_normalized_chinese(self, lowered: str) -> str | None:
        if ("酸痛" in lowered or "肌肉酸痛" in lowered) and any(
            term in lowered for term in ["训练后", "练后", "恢复", "缓解", "怎么"]
        ):
            return "recovery_check"
        if any(term in lowered for term in [
            "疼", "痛", "刺痛", "胸闷", "胸口闷", "头晕", "呼吸困难", "麻木", "手麻",
            "困难", "受伤", "甲亢", "甲状腺", "吃药", "服药", "用药",
        ]):
            return "injury_or_risk"
        if any(term in lowered for term in ["月复盘", "月度复盘", "月度", "月总结"]):
            return "monthly_review"
        if any(term in lowered for term in ["周复盘", "本周", "周总结"]):
            return "weekly_review"
        if self._is_normalized_plan_request(lowered):
            return "training_plan"
        if any(term in lowered for term in ["加重", "加重量", "进步", "下次", "降载"]):
            return "progression_decision"
        if any(term in lowered for term in [
            "完成", "做完", "练了", "练完", "今天练", "kg", "公斤", "rpe", "组", "次数",
            "卧推", "深蹲", "硬拉",
        ]):
            return "training_log"
        if any(term in lowered for term in [
            "吃", "热量", "蛋白", "蛋白质", "碳水", "脂肪", "外卖", "外食", "饮食", "饮食方案",
        ]):
            return "nutrition_advice"
        if any(term in lowered for term in ["睡", "疲劳", "酸痛", "肌肉酸痛", "恢复", "压力", "心率"]):
            return "recovery_check"
        if any(term in lowered for term in ["你记得", "还记得", "我的档案", "记忆"]):
            return "memory_query"
        return None

    def _is_normalized_plan_request(self, lowered: str) -> bool:
        negative_terms = ["不要", "不需要", "不用", "别", "不要给", "不要生成"]
        plan_terms = ["计划", "训练计划", "健身计划"]
        if any(neg in lowered for neg in negative_terms) and any(term in lowered for term in plan_terms):
            return False
        return any(term in lowered for term in [
            "训练计划", "健身计划", "生成计划", "制定计划", "做个计划", "出个计划",
            "给我计划", "帮我计划", "安排训练", "今天练什么", "今天应该练什么",
            "今天应该干什么", "今天干什么", "今日训练", "练什么",
        ])

    def is_plan_request(self, message: str) -> bool:
        lowered = message.lower()
        negative_terms = ["不要", "不需要", "不用", "别", "不要给", "别给", "不要生成", "不要带", "不要再"]
        plan_terms = ["计划", "训练计划", "健身计划", "workout plan", "training plan"]
        if any(neg in lowered for neg in negative_terms) and any(term in lowered for term in plan_terms):
            return False
        return self._contains(lowered, self.PLAN_REQUEST_TERMS)


IntentRouter = StructuredIntentRouter


class FitnessRetrievalService:
    """User-scoped structured and semantic retrieval for ContextBuilder."""

    def __init__(self, db: Session, model_provider: ModelProvider | None = None):
        self.db = db
        self.memory_manager = MemoryManager(db, model_provider)

    def get_core_profile(self, user_id: uuid.UUID) -> dict[str, Any]:
        profile = self.db.get(models.UserProfile, user_id)
        if profile is None:
            return {}
        return {
            "age": profile.age,
            "sex": profile.sex,
            "height_cm": profile.height_cm,
            "weight_kg": profile.weight_kg,
            "activity_level": profile.activity_level,
            "goal": profile.goal,
            "experience_level": profile.experience_level,
            "workout_frequency": profile.workout_frequency,
            "equipment_available": profile.equipment_available or [],
            "dietary_preferences": profile.dietary_preferences or [],
            "injuries": profile.injuries or [],
            "target_calories": profile.target_calories,
            "target_protein_g": profile.target_protein_g,
            "target_carbs_g": profile.target_carbs_g,
            "target_fat_g": profile.target_fat_g,
        }

    def get_active_plan(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        plan = self.db.scalar(
            select(models.TrainingPlan)
            .where(models.TrainingPlan.user_id == user_id, models.TrainingPlan.status == "active")
            .order_by(desc(models.TrainingPlan.created_at))
        )
        if plan is None:
            return None
        return {
            "id": str(plan.id),
            "status": plan.status,
            "week_start": plan.week_start.isoformat() if plan.week_start else None,
            "plan": plan.plan_json,
            "rationale": plan.rationale,
        }

    def get_recent_workout_logs(self, user_id: uuid.UUID, days: int = 14, limit: int = 10) -> list[dict[str, Any]]:
        since = datetime.utcnow() - timedelta(days=days)
        logs = list(
            self.db.scalars(
                select(models.WorkoutLog)
                .where(models.WorkoutLog.user_id == user_id, models.WorkoutLog.performed_at >= since)
                .order_by(desc(models.WorkoutLog.performed_at))
                .limit(limit)
            )
        )
        return [
            {
                "performed_at": log.performed_at.isoformat() if log.performed_at else None,
                "workout_name": log.workout_name,
                "duration_minutes": log.duration_minutes,
                "rpe": log.rpe,
                "completion_rate": log.completion_rate,
                "exercises": log.exercises,
                "notes": log.notes,
            }
            for log in logs
        ]

    def get_exercise_history(self, user_id: uuid.UUID, exercise_name: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        filters = [models.ExerciseLog.user_id == user_id]
        if exercise_name:
            filters.append(models.ExerciseLog.exercise_name.ilike(f"%{exercise_name}%"))
        logs = list(
            self.db.scalars(
                select(models.ExerciseLog)
                .where(*filters)
                .order_by(desc(models.ExerciseLog.created_at), desc(models.ExerciseLog.set_index))
                .limit(limit)
            )
        )
        return [
            {
                "exercise_name": log.exercise_name,
                "set_index": log.set_index,
                "reps": log.reps,
                "weight": log.weight,
                "rpe": log.rpe,
                "completed": log.completed,
                "pain_score": log.pain_score,
                "pain_location": log.pain_location,
                "notes": log.notes,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]

    def get_recent_nutrition_summary(self, user_id: uuid.UUID, days: int = 7) -> list[dict[str, Any]]:
        since = date.today() - timedelta(days=days)
        summaries = list(
            self.db.scalars(
                select(models.NutritionDailySummary)
                .where(
                    models.NutritionDailySummary.user_id == user_id,
                    models.NutritionDailySummary.summary_date >= since,
                )
                .order_by(desc(models.NutritionDailySummary.summary_date))
            )
        )
        return [
            {
                "date": item.summary_date.isoformat(),
                "total_calories": item.total_calories,
                "total_protein_g": item.total_protein_g,
                "target_calories": item.target_calories,
                "target_protein_g": item.target_protein_g,
                "adherence_score": item.adherence_score,
                "summary_text": item.summary_text,
            }
            for item in summaries
        ]

    def get_recent_recovery_logs(self, user_id: uuid.UUID, days: int = 7) -> list[dict[str, Any]]:
        since = date.today() - timedelta(days=days)
        logs = list(
            self.db.scalars(
                select(models.RecoveryLog)
                .where(models.RecoveryLog.user_id == user_id, models.RecoveryLog.log_date >= since)
                .order_by(desc(models.RecoveryLog.log_date))
            )
        )
        return [
            {
                "date": log.log_date.isoformat(),
                "sleep_hours": log.sleep_hours,
                "sleep_quality_score": log.sleep_quality_score,
                "fatigue_score": log.fatigue_score,
                "soreness_score": log.soreness_score,
                "stress_score": log.stress_score,
                "resting_hr": log.resting_hr,
                "notes": log.notes,
            }
            for log in logs
        ]

    def get_recent_symptom_logs(self, user_id: uuid.UUID, days: int = 14) -> list[dict[str, Any]]:
        since = date.today() - timedelta(days=days)
        logs = list(
            self.db.scalars(
                select(models.SymptomLog)
                .where(models.SymptomLog.user_id == user_id, models.SymptomLog.symptom_date >= since)
                .order_by(desc(models.SymptomLog.symptom_date), desc(models.SymptomLog.created_at))
            )
        )
        return [
            {
                "date": log.symptom_date.isoformat(),
                "body_part": log.body_part,
                "symptom_type": log.symptom_type,
                "severity_score": log.severity_score,
                "trigger_context": log.trigger_context,
                "action_taken": log.action_taken,
                "status": log.status,
            }
            for log in logs
        ]

    def get_active_risk_notes(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        notes = self.memory_manager.get_active_risk_notes(user_id)
        return [
            {
                "body_part": note.body_part,
                "risk_type": note.risk_type,
                "description": note.description,
                "severity_score": note.severity_score,
                "confidence_score": note.confidence_score,
                "status": note.status,
                "last_seen_at": note.last_seen_at.isoformat() if note.last_seen_at else None,
            }
            for note in notes
        ]

    def get_memory_catalog(self, user_id: uuid.UUID, category: str | None = None) -> list[dict[str, Any]]:
        entries = self.memory_manager.get_memory_catalog(user_id, category=category)
        return [
            {
                "category": entry.category,
                "title": entry.title,
                "summary": entry.summary,
                "record_count": entry.record_count,
                "importance_score": entry.importance_score,
                "query_hints": entry.query_hints,
                "child_table": entry.child_table,
                "child_filter": entry.child_filter,
            }
            for entry in entries
        ]

    def search_relevant_memories(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int = 6,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        memories = self.memory_manager.search_memories(user_id, query, top_k=top_k, category=category)
        return [self._memory_payload(memory) for memory in memories]

    def search_planned_memories(
        self,
        user_id: uuid.UUID,
        query: str,
        plan: MemoryRecallPlan,
    ) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for search in plan.searches:
            memories = self.memory_manager.search_memories(
                user_id,
                query,
                top_k=search.top_k,
                category=search.category if search.category is not None else plan.category,
                memory_network=search.memory_network,
                fact_kind=search.fact_kind,
            )
            for memory in memories:
                key = str(memory.id)
                if key in by_key:
                    by_key[key].setdefault("retrieval_plan_labels", []).append(search.label)
                    continue
                payload = self._memory_payload(memory)
                payload["retrieval_plan_label"] = search.label
                payload["retrieval_plan_labels"] = [search.label]
                payload["retrieval_plan_rationale"] = search.rationale
                by_key[key] = payload
        return list(by_key.values())[: plan.top_k]

    def _memory_payload(self, memory: models.LongTermMemory) -> dict[str, Any]:
        return {
            "id": str(memory.id),
            "memory_type": memory.memory_type,
            "memory_network": getattr(memory, "memory_network", "world"),
            "fact_kind": getattr(memory, "fact_kind", "unknown"),
            "category": memory.category,
            "summary": memory.summary,
            "content": memory.content,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "entities": getattr(memory, "entities", None) or [],
            "evidence": getattr(memory, "evidence", None) or [],
            "semantic_rank": getattr(memory, "semantic_rank", None),
            "keyword_rank": getattr(memory, "keyword_rank", None),
            "entity_rank": getattr(memory, "entity_rank", None),
            "temporal_rank": getattr(memory, "temporal_rank", None),
            "final_score": getattr(memory, "final_score", None),
            "retrieval_debug": getattr(memory, "retrieval_debug", None),
            "metadata": memory.memory_metadata or {},
        }


class ContextBuilder:
    """Build small, intent-specific context packets for the coach LLM."""

    CONTEXT_LIMITS = {
        "memory_catalog": 6,
        "recent_training": 6,
        "exercise_history": 8,
        "recent_nutrition": 5,
        "recent_recovery": 5,
        "recent_symptoms": 8,
        "world_memories": 4,
        "experience_memories": 3,
        "observation_memories": 3,
        "opinion_memories": 2,
    }
    KNOWLEDGE_LIMITS = {
        "explanation_knowledge": 2,
        "plan_templates": 2,
        "coaching_cases": 1,
    }
    MEMORY_GROUP_ORDER = [
        "world_memories",
        "experience_memories",
        "observation_memories",
        "opinion_memories",
    ]

    CATEGORY_BY_INTENT = {
        "training_log": "training",
        "training_plan": None,
        "progression_decision": "training",
        "nutrition_advice": "nutrition",
        "nutrition_log": "nutrition",
        "recovery_check": "recovery",
        "injury_or_risk": "risk",
        "weekly_review": None,
        "monthly_review": None,
        "memory_query": None,
        "general_chat": None,
    }

    def __init__(
        self,
        db: Session,
        model_provider: ModelProvider | None = None,
        intent_router: IntentRouter | None = None,
    ):
        self.intent_router = intent_router or IntentRouter()
        self.retrieval = FitnessRetrievalService(db, model_provider)
        self.knowledge = FitnessKnowledgeService(db, model_provider)
        self.memory_planner = MemoryPlanner()

    def build_context_packet(
        self,
        user_id: uuid.UUID,
        user_message: str,
        intent: str | None = None,
    ) -> dict[str, Any]:
        intent_decision = self._build_intent_decision(user_message, intent)
        selected_intent = intent_decision.primary_intent
        category = self.CATEGORY_BY_INTENT.get(selected_intent)
        core_profile = self.retrieval.get_core_profile(user_id)
        active_risk_notes = self.retrieval.get_active_risk_notes(user_id)
        if intent is None and getattr(self.intent_router, "analyze", None):
            intent_decision = self.intent_router.analyze(
                user_message,
                profile=self._profile_object_for_intent(core_profile),
            )
            selected_intent = intent_decision.primary_intent
            category = self.CATEGORY_BY_INTENT.get(selected_intent)
        memory_planner = getattr(self, "memory_planner", MemoryPlanner())
        recall_plan = memory_planner.build_plan(
            selected_intent,
            user_message,
            category,
            core_profile=core_profile,
            active_risk_notes=active_risk_notes,
        )
        category = recall_plan.category
        allow_plan_content = intent_decision.allowed_actions.get("allow_plan_content", selected_intent in {
            "training_plan",
            "training_log",
            "progression_decision",
            "recovery_check",
            "weekly_review",
            "monthly_review",
        })
        current_request_policy = {
            "current_intent": selected_intent,
            "secondary_intents": intent_decision.secondary_intents,
            "should_generate_plan": intent_decision.allowed_actions.get(
                "generate_plan",
                selected_intent == "training_plan",
            ),
            "allow_plan_content": allow_plan_content,
            "risk_level": intent_decision.risk_level,
            "needs_clarification": intent_decision.needs_clarification,
            "missing_slots": intent_decision.missing_slots,
            "allowed_actions": intent_decision.allowed_actions,
            "history_scope": (
                "Use prior conversation, memories, and active plans only as background. "
                "Do not continue or execute older user commands unless the current user_message explicitly asks for it."
            ),
        }

        packet: dict[str, Any] = {
            "intent": selected_intent,
            "intent_decision": intent_decision.to_dict(),
            "secondary_intents": intent_decision.secondary_intents,
            "intent_entities": intent_decision.entities,
            "current_request_policy": current_request_policy,
            "core_profile": core_profile,
            "memory_catalog": self.retrieval.get_memory_catalog(user_id, category=category),
            "active_plan": self.retrieval.get_active_plan(user_id) if allow_plan_content else None,
            "active_risk_notes": active_risk_notes,
            "recent_training": [],
            "exercise_history": [],
            "recent_nutrition": [],
            "recent_recovery": [],
            "recent_symptoms": [],
            "relevant_memories": self._retrieve_memories(user_id, user_message, recall_plan),
            "world_memories": [],
            "experience_memories": [],
            "observation_memories": [],
            "opinion_memories": [],
            "knowledge_context": {},
            "strategy_memory_guidance": {},
            "retrieval_debug": {
                "intent": selected_intent,
                "intent_decision": intent_decision.to_dict(),
                "memory_category_filter": category,
                "memory_top_k": recall_plan.top_k,
                "memory_ranker": "hybrid_vector_bm25",
                "memory_recall_plan": recall_plan.to_dict(),
                "current_request_policy": current_request_policy,
                "knowledge_sources": {},
            },
            "agent_decision_history": [],
            "context_summary": "",
        }
        self._group_hindsight_memories(packet)

        if selected_intent in {"training_log", "training_plan", "progression_decision", "weekly_review", "monthly_review"}:
            packet["recent_training"] = self.retrieval.get_recent_workout_logs(user_id, days=14)
            packet["exercise_history"] = self.retrieval.get_exercise_history(user_id, self._extract_exercise_name(user_message))

        if selected_intent in {"nutrition_advice", "nutrition_log", "weekly_review", "monthly_review"}:
            packet["recent_nutrition"] = self.retrieval.get_recent_nutrition_summary(user_id, days=7)

        if selected_intent in {"recovery_check", "progression_decision", "injury_or_risk", "weekly_review", "monthly_review"}:
            packet["recent_recovery"] = self.retrieval.get_recent_recovery_logs(user_id, days=7)

        if selected_intent in {"injury_or_risk", "progression_decision", "weekly_review", "monthly_review"}:
            packet["recent_symptoms"] = self.retrieval.get_recent_symptom_logs(user_id, days=14)

        packet["knowledge_context"] = self.knowledge.build_knowledge_context(
            selected_intent,
            user_message,
            packet,
        )
        self._apply_budget_policy(packet)
        packet["strategy_memory_guidance"] = self._build_strategy_memory_guidance(packet)
        packet["retrieval_debug"]["knowledge_sources"] = packet["knowledge_context"].get("debug", {})
        packet["context_summary"] = self._summarize_packet(packet)
        return packet

    def _build_intent_decision(self, user_message: str, intent: str | None) -> IntentDecision:
        if intent:
            if hasattr(self.intent_router, "from_intent"):
                return self.intent_router.from_intent(intent)
            return IntentDecision(primary_intent=intent)
        if hasattr(self.intent_router, "analyze"):
            return self.intent_router.analyze(user_message)
        return IntentDecision(primary_intent=self.intent_router.classify(user_message))

    def _profile_object_for_intent(self, core_profile: dict[str, Any]) -> Any | None:
        if not core_profile:
            return None
        return SimpleNamespace(**core_profile)

    def _retrieve_memories(
        self,
        user_id: uuid.UUID,
        user_message: str,
        recall_plan: MemoryRecallPlan,
    ) -> list[dict[str, Any]]:
        if hasattr(self.retrieval, "search_planned_memories"):
            return self.retrieval.search_planned_memories(user_id, user_message, recall_plan)
        return self.retrieval.search_relevant_memories(
            user_id,
            user_message,
            top_k=recall_plan.top_k,
            category=recall_plan.category,
        )

    def _group_hindsight_memories(self, packet: dict[str, Any]) -> None:
        grouped = {
            "world": "world_memories",
            "experience": "experience_memories",
            "observation": "observation_memories",
            "opinion": "opinion_memories",
        }
        for memory in packet.get("relevant_memories") or []:
            target = grouped.get(memory.get("memory_network") or "world", "world_memories")
            if target == "opinion_memories":
                memory["evidence_summary"] = self._evidence_summary(memory.get("evidence") or [])
            packet[target].append(memory)

    def _evidence_summary(self, evidence: list[dict[str, Any]]) -> str:
        if not evidence:
            return "No evidence attached."
        parts = []
        for item in evidence[:5]:
            table = item.get("table", "unknown")
            summary = item.get("summary") or item.get("id") or "evidence"
            time = item.get("time")
            parts.append(f"{table}: {summary}" + (f" ({time})" if time else ""))
        return " | ".join(parts)

    def _build_strategy_memory_guidance(self, packet: dict[str, Any]) -> dict[str, Any]:
        successful: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for memory in packet.get("experience_memories") or []:
            fact_kind = memory.get("fact_kind")
            if fact_kind not in {"strategy_experience", "failed_strategy"}:
                continue
            item = {
                "id": memory.get("id"),
                "fact_kind": fact_kind,
                "category": memory.get("category"),
                "summary": memory.get("summary") or memory.get("content"),
                "content": memory.get("content"),
                "evidence_summary": self._evidence_summary(memory.get("evidence") or []),
                "retrieval_plan_labels": memory.get("retrieval_plan_labels")
                or ([memory.get("retrieval_plan_label")] if memory.get("retrieval_plan_label") else []),
            }
            if fact_kind == "strategy_experience":
                successful.append(
                    {
                        **item,
                        "usage": "reuse only when current state, constraints, and evidence are similar",
                    }
                )
            else:
                failed.append(
                    {
                        **item,
                        "usage": "avoid repeating unless current state has clearly changed",
                    }
                )
        return {
            "successful_strategies": successful[:3],
            "failed_strategies": failed[:3],
            "policy": (
                "Use successful_strategies as outcome-backed examples for similar contexts. "
                "Treat failed_strategies as prior approaches to avoid. active_risk_notes and decision_rules override both."
            ),
        }

    def _apply_budget_policy(self, packet: dict[str, Any]) -> None:
        debug = packet.setdefault("retrieval_debug", {})
        debug["context_budget_policy"] = {
            "protected": ["active_risk_notes", "decision_rules", "core_profile", "current_request_policy"],
            "limits": {**self.CONTEXT_LIMITS, **{f"knowledge_context.{key}": value for key, value in self.KNOWLEDGE_LIMITS.items()}},
            "priority": [
                "active_risk_notes",
                "core_profile",
                "current_request_policy",
                "decision_rules",
                "world_memories",
                "experience_memories",
                "observation_memories",
                "opinion_memories",
                "personal_recent_state",
                "knowledge_context",
            ],
        }
        dropped: dict[str, dict[str, Any]] = {}
        for key, limit in self.CONTEXT_LIMITS.items():
            original = packet.get(key) or []
            if not isinstance(original, list):
                continue
            kept = original[:limit]
            packet[key] = kept
            if len(original) > len(kept):
                dropped[key] = {
                    "dropped_count": len(original) - len(kept),
                    "reason": f"limited to {limit} items by ContextBuilder budget policy",
                }

        packet["relevant_memories"] = [
            memory
            for group_key in self.MEMORY_GROUP_ORDER
            for memory in (packet.get(group_key) or [])
        ]

        knowledge = packet.get("knowledge_context") or {}
        knowledge_dropped: dict[str, dict[str, Any]] = {}
        for key, limit in self.KNOWLEDGE_LIMITS.items():
            original = knowledge.get(key) or []
            if not isinstance(original, list):
                continue
            kept = original[:limit]
            knowledge[key] = kept
            if len(original) > len(kept):
                knowledge_dropped[key] = {
                    "dropped_count": len(original) - len(kept),
                    "reason": (
                        f"knowledge_context.{key} limited to {limit} items so knowledge does not displace "
                        "core profile, active risks, or recent user state"
                    ),
                }
        if knowledge_dropped:
            dropped["knowledge_context"] = knowledge_dropped
        debug["dropped_candidates"] = dropped
        debug["loaded_counts"] = self._context_counts(packet)

    def _extract_exercise_name(self, message: str) -> str | None:
        mapping = {
            "bench_press": ["鍗ф帹", "bench"],
            "squat": ["娣辫共", "squat"],
            "deadlift": ["纭媺", "deadlift"],
            "pull_up": ["寮曚綋", "pull-up", "pullup"],
            "overhead_press": ["鎺ㄨ偐", "shoulder press"],
        }
        lowered = message.lower()
        normalized_mapping = {
            "bench_press": ["卧推", "bench", "bench press"],
            "squat": ["深蹲", "squat"],
            "deadlift": ["硬拉", "deadlift"],
            "pull_up": ["引体", "引体向上", "pull-up", "pullup"],
            "overhead_press": ["推举", "肩推", "shoulder press", "overhead press"],
        }
        for normalized, terms in normalized_mapping.items():
            if any(term in lowered for term in terms):
                return normalized
        for normalized, terms in mapping.items():
            if any(term in lowered for term in terms):
                return normalized
        return None

    def _summarize_packet(self, packet: dict[str, Any]) -> str:
        counts = self._context_counts(packet)
        loaded = [f"{key}={value}" for key, value in counts.items()]
        return f"intent={packet['intent']}; loaded " + ", ".join(loaded)

    def _context_counts(self, packet: dict[str, Any]) -> dict[str, int]:
        keys = [
            "core_profile",
            "memory_catalog",
            "active_plan",
            "active_risk_notes",
            "recent_training",
            "exercise_history",
            "recent_nutrition",
            "recent_recovery",
            "recent_symptoms",
            "world_memories",
            "experience_memories",
            "observation_memories",
            "opinion_memories",
            "relevant_memories",
        ]
        counts: dict[str, int] = {}
        for key in keys:
            value = packet.get(key)
            if isinstance(value, list):
                counts[key] = len(value)
            elif isinstance(value, dict):
                counts[key] = 1 if value else 0
            elif value:
                counts[key] = 1
            else:
                counts[key] = 0
        knowledge = packet.get("knowledge_context") or {}
        for key in ["decision_rules", "explanation_knowledge", "plan_templates", "coaching_cases"]:
            value = knowledge.get(key) or []
            counts[f"knowledge_context.{key}"] = len(value) if isinstance(value, list) else (1 if value else 0)
        return counts
