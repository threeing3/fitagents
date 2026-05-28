import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.fitness_knowledge import FitnessKnowledgeService
from fast_api.app.services.memory_system import MemoryManager
from fast_api.app.services.model_provider import ModelProvider


class IntentRouter:
    """Small rule-first intent classifier for the fitness coach MVP."""

    TRAINING_LOG_TERMS = ["完成", "做完", "练了", "kg", "公斤", "rpe", "组", "次数", "bench", "squat", "deadlift"]
    PROGRESSION_TERMS = ["加重", "重量", "进步", "下次", "progress", "increase", "deload", "降载"]
    NUTRITION_TERMS = ["吃", "热量", "蛋白", "碳水", "脂肪", "外卖", "外食", "calorie", "protein", "diet"]
    RECOVERY_TERMS = ["睡", "疲劳", "酸痛", "恢复", "压力", "心率", "recovery", "sleep", "tired"]
    RISK_TERMS = ["疼", "痛", "刺痛", "胸闷", "头晕", "呼吸困难", "麻木", "受伤", "甲亢", "甲状腺", "吃药", "服药", "用药", "injury", "pain", "dizzy"]
    REVIEW_TERMS = ["周复盘", "本周", "weekly", "月复盘", "monthly", "总结"]
    MEMORY_TERMS = ["你记得", "我的档案", "记忆", "memory", "profile"]

    def classify(self, message: str) -> str:
        lowered = message.lower()
        if self._contains(lowered, self.RISK_TERMS):
            return "injury_or_risk"
        if self._contains(lowered, self.REVIEW_TERMS):
            return "monthly_review" if "月" in lowered or "monthly" in lowered else "weekly_review"
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
        return [
            {
                "id": str(memory.id),
                "memory_type": memory.memory_type,
                "category": memory.category,
                "summary": memory.summary,
                "content": memory.content,
                "importance": memory.importance,
                "confidence": memory.confidence,
                "metadata": memory.memory_metadata or {},
            }
            for memory in memories
        ]


class ContextBuilder:
    """Build small, intent-specific context packets for the coach LLM."""

    CATEGORY_BY_INTENT = {
        "training_log": "training",
        "training_plan": "plan",
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

    def build_context_packet(
        self,
        user_id: uuid.UUID,
        user_message: str,
        intent: str | None = None,
    ) -> dict[str, Any]:
        selected_intent = intent or self.intent_router.classify(user_message)
        category = self.CATEGORY_BY_INTENT.get(selected_intent)

        packet: dict[str, Any] = {
            "intent": selected_intent,
            "core_profile": self.retrieval.get_core_profile(user_id),
            "memory_catalog": self.retrieval.get_memory_catalog(user_id, category=category),
            "active_plan": self.retrieval.get_active_plan(user_id),
            "active_risk_notes": self.retrieval.get_active_risk_notes(user_id),
            "recent_training": [],
            "exercise_history": [],
            "recent_nutrition": [],
            "recent_recovery": [],
            "recent_symptoms": [],
            "relevant_memories": self.retrieval.search_relevant_memories(
                user_id,
                user_message,
                top_k=6,
                category=category,
            ),
            "knowledge_context": {},
            "retrieval_debug": {
                "intent": selected_intent,
                "memory_category_filter": category,
                "memory_top_k": 6,
                "knowledge_sources": {},
            },
            "agent_decision_history": [],
            "context_summary": "",
        }

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
        packet["retrieval_debug"]["knowledge_sources"] = packet["knowledge_context"].get("debug", {})
        packet["context_summary"] = self._summarize_packet(packet)
        return packet

    def _extract_exercise_name(self, message: str) -> str | None:
        mapping = {
            "bench_press": ["卧推", "bench"],
            "squat": ["深蹲", "squat"],
            "deadlift": ["硬拉", "deadlift"],
            "pull_up": ["引体", "pull-up", "pullup"],
            "overhead_press": ["推肩", "shoulder press"],
        }
        lowered = message.lower()
        for normalized, terms in mapping.items():
            if any(term in lowered for term in terms):
                return normalized
        return None

    def _summarize_packet(self, packet: dict[str, Any]) -> str:
        loaded = []
        for key in [
            "core_profile",
            "memory_catalog",
            "active_plan",
            "active_risk_notes",
            "recent_training",
            "exercise_history",
            "recent_nutrition",
            "recent_recovery",
            "recent_symptoms",
            "relevant_memories",
            "knowledge_context",
        ]:
            value = packet.get(key)
            if value:
                loaded.append(f"{key}={len(value) if isinstance(value, list) else 1}")
        return f"intent={packet['intent']}; loaded " + ", ".join(loaded)
