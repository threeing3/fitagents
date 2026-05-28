import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.model_provider import ModelProvider


class MemoryManager:
    """Write, catalog, and retrieve user-scoped long-term fitness memories."""

    def __init__(self, db: Session, model_provider: ModelProvider | None = None):
        self.db = db
        self.model_provider = model_provider or ModelProvider()

    def add_memory(
        self,
        user_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> models.LongTermMemory:
        return self.create_memory_item(
            user_id=user_id,
            memory_type=payload.get("memory_type", "episodic"),
            category=payload.get("category"),
            content=payload["content"],
            summary=payload.get("summary"),
            importance_score=float(payload.get("importance_score", payload.get("importance", 0.6))),
            confidence_score=float(payload.get("confidence_score", payload.get("confidence", 0.75))),
            source_type=payload.get("source_type", payload.get("source", "manual")),
            source_id=payload.get("source_id"),
            metadata=payload.get("metadata") or payload.get("memory_metadata") or {},
        )

    def create_memory_item(
        self,
        user_id: uuid.UUID,
        memory_type: str,
        category: str | None,
        content: str,
        summary: str | None = None,
        importance_score: float = 0.6,
        confidence_score: float = 0.75,
        source_type: str = "manual",
        source_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> models.LongTermMemory:
        memory = models.LongTermMemory(
            user_id=user_id,
            memory_type=memory_type,
            category=category or self._category_from_type(memory_type),
            content=content,
            summary=summary or self._compact_summary(content),
            importance=importance_score,
            recency_score=1.0,
            confidence=confidence_score,
            source=source_type,
            memory_metadata={
                **(metadata or {}),
                **({"source_id": str(source_id)} if source_id else {}),
            },
            embedding=self.model_provider.embed_text(f"{category or memory_type}\n{summary or content}\n{content}"),
        )
        self.db.add(memory)
        self.db.flush()
        self.update_memory_catalog(user_id, memory.category or memory_type)
        self.update_memory_blocks(user_id)
        return memory

    def update_memory_block(
        self,
        user_id: uuid.UUID,
        block_type: str,
        content: str,
        title: str | None = None,
        token_budget: int = 240,
        importance_score: float = 0.7,
    ) -> models.MemoryBlock:
        block = self.db.scalar(
            select(models.MemoryBlock).where(
                models.MemoryBlock.user_id == user_id,
                models.MemoryBlock.block_type == block_type,
            )
        )
        if block is None:
            block = models.MemoryBlock(
                user_id=user_id,
                block_type=block_type,
                title=title or block_type.replace("_", " ").title(),
                content=self._trim(content, token_budget * 4),
                token_budget=token_budget,
                importance_score=importance_score,
            )
            self.db.add(block)
        else:
            block.title = title or block.title
            block.content = self._trim(content, token_budget * 4)
            block.token_budget = token_budget
            block.importance_score = importance_score
            block.version = (block.version or 1) + 1
        self.db.flush()
        return block

    def update_memory_blocks(self, user_id: uuid.UUID) -> None:
        profile = self.db.get(models.UserProfile, user_id)
        if profile:
            parts = [
                f"goal={profile.goal or 'unknown'}",
                f"experience={profile.experience_level or 'unknown'}",
                f"body={profile.height_cm or '?'}cm/{profile.weight_kg or '?'}kg",
                f"frequency={profile.workout_frequency or '?'} per week",
                f"equipment={', '.join(profile.equipment_available or []) or 'unknown'}",
                f"injuries={', '.join(profile.injuries or []) or 'none'}",
            ]
            self.update_memory_block(user_id, "profile", "; ".join(parts), "Core Profile", 220, 0.9)

        risk_notes = self.get_active_risk_notes(user_id)
        risk_content = "No active risk notes."
        if risk_notes:
            risk_content = "\n".join(f"- {note.risk_type}: {note.description}" for note in risk_notes[:5])
        self.update_memory_block(user_id, "risk", risk_content, "Risk Notes", 180, 0.95)

        preference_memories = self.list_memories(
            user_id,
            category="preference",
            limit=5,
        )
        if preference_memories:
            content = "\n".join(f"- {item.summary or item.content}" for item in preference_memories)
            self.update_memory_block(user_id, "preference", content, "Preferences", 180, 0.75)

    def update_memory_catalog(self, user_id: uuid.UUID, category: str) -> models.MemoryCatalog:
        memories = list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(
                    models.LongTermMemory.user_id == user_id,
                    models.LongTermMemory.status == "active",
                    models.LongTermMemory.category == category,
                )
                .order_by(desc(models.LongTermMemory.created_at))
                .limit(20)
            )
        )
        summary = self._catalog_summary(category, memories)
        catalog = self.db.scalar(
            select(models.MemoryCatalog).where(
                models.MemoryCatalog.user_id == user_id,
                models.MemoryCatalog.category == category,
                models.MemoryCatalog.title == self._catalog_title(category),
            )
        )
        if catalog is None:
            catalog = models.MemoryCatalog(
                user_id=user_id,
                category=category,
                title=self._catalog_title(category),
                summary=summary,
                importance_score=max([m.importance or 0.5 for m in memories], default=0.5),
                record_count=len(memories),
                query_hints=self._query_hints(category),
                child_table=self._child_table_for_category(category),
                child_filter={"category": category},
            )
            self.db.add(catalog)
        else:
            catalog.summary = summary
            catalog.record_count = len(memories)
            catalog.importance_score = max([m.importance or 0.5 for m in memories], default=0.5)
            catalog.last_updated_at = datetime.utcnow()
        self.db.flush()
        return catalog

    def search_memories(
        self,
        user_id: uuid.UUID,
        query: str,
        top_k: int = 6,
        category: str | None = None,
        memory_type: str | None = None,
    ) -> list[models.LongTermMemory]:
        filters = [
            models.LongTermMemory.user_id == user_id,
            models.LongTermMemory.status == "active",
        ]
        if category:
            filters.append(models.LongTermMemory.category == category)
        if memory_type:
            filters.append(models.LongTermMemory.memory_type == memory_type)

        vector_candidates: list[models.LongTermMemory] = []
        try:
            query_embedding = self.model_provider.embed_text(query)
            vector_candidates = list(
                self.db.scalars(
                    select(models.LongTermMemory)
                    .where(*filters, models.LongTermMemory.embedding.is_not(None))
                    .order_by(models.LongTermMemory.embedding.cosine_distance(query_embedding))
                    .limit(top_k * 3)
                )
            )
        except Exception:
            vector_candidates = []

        recent_candidates = list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(*filters)
                .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                .limit(top_k * 3)
            )
        )
        by_id = {memory.id: memory for memory in vector_candidates}
        for memory in recent_candidates:
            by_id.setdefault(memory.id, memory)
        ranked = sorted(
            by_id.values(),
            key=lambda item: self._memory_score(item, query),
            reverse=True,
        )[:top_k]
        for memory in ranked:
            self.mark_memory_accessed(memory)
        return ranked

    def list_memories(
        self,
        user_id: uuid.UUID,
        category: str | None = None,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[models.LongTermMemory]:
        filters = [
            models.LongTermMemory.user_id == user_id,
            models.LongTermMemory.status == "active",
        ]
        if category:
            filters.append(models.LongTermMemory.category == category)
        if memory_type:
            filters.append(models.LongTermMemory.memory_type == memory_type)
        return list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(*filters)
                .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                .limit(limit)
            )
        )

    def get_memory_catalog(
        self,
        user_id: uuid.UUID,
        category: str | None = None,
        limit: int = 20,
    ) -> list[models.MemoryCatalog]:
        filters = [models.MemoryCatalog.user_id == user_id]
        if category:
            filters.append(models.MemoryCatalog.category == category)
        return list(
            self.db.scalars(
                select(models.MemoryCatalog)
                .where(*filters)
                .order_by(desc(models.MemoryCatalog.importance_score), desc(models.MemoryCatalog.last_updated_at))
                .limit(limit)
            )
        )

    def get_memory_blocks(self, user_id: uuid.UUID) -> list[models.MemoryBlock]:
        return list(
            self.db.scalars(
                select(models.MemoryBlock)
                .where(models.MemoryBlock.user_id == user_id)
                .order_by(desc(models.MemoryBlock.importance_score), models.MemoryBlock.block_type)
            )
        )

    def get_active_risk_notes(self, user_id: uuid.UUID, limit: int = 10) -> list[models.RiskNote]:
        return list(
            self.db.scalars(
                select(models.RiskNote)
                .where(models.RiskNote.user_id == user_id, models.RiskNote.status.in_(["active", "monitoring"]))
                .order_by(desc(models.RiskNote.severity_score), desc(models.RiskNote.last_seen_at))
                .limit(limit)
            )
        )

    def mark_memory_accessed(self, memory: models.LongTermMemory) -> None:
        memory.last_accessed_at = datetime.utcnow()
        memory.access_count = (memory.access_count or 0) + 1

    def expire_old_memories(self, user_id: uuid.UUID) -> int:
        now = datetime.utcnow()
        memories = list(
            self.db.scalars(
                select(models.LongTermMemory).where(
                    models.LongTermMemory.user_id == user_id,
                    models.LongTermMemory.status == "active",
                    models.LongTermMemory.valid_until.is_not(None),
                    models.LongTermMemory.valid_until < now,
                )
            )
        )
        for memory in memories:
            memory.status = "expired"
        self.db.flush()
        return len(memories)

    def consolidate_memories(self, user_id: uuid.UUID) -> dict[str, Any]:
        categories = [
            row[0]
            for row in self.db.execute(
                select(models.LongTermMemory.category)
                .where(models.LongTermMemory.user_id == user_id, models.LongTermMemory.status == "active")
                .group_by(models.LongTermMemory.category)
            )
            if row[0]
        ]
        for category in categories:
            self.update_memory_catalog(user_id, category)
        self.update_memory_blocks(user_id)
        return {"categories": categories, "catalog_entries_updated": len(categories)}

    def write_daily_summary(self, user_id: uuid.UUID, summary_date: date) -> models.LongTermMemory:
        workout_count = self.db.scalar(
            select(func.count(models.WorkoutSession.id)).where(
                models.WorkoutSession.user_id == user_id,
                models.WorkoutSession.session_date == summary_date,
            )
        )
        symptoms = self.db.scalar(
            select(func.count(models.SymptomLog.id)).where(
                models.SymptomLog.user_id == user_id,
                models.SymptomLog.symptom_date == summary_date,
            )
        )
        content = (
            f"Daily summary {summary_date}: workouts={workout_count or 0}, "
            f"symptom_events={symptoms or 0}."
        )
        return self.create_memory_item(
            user_id=user_id,
            memory_type="episodic",
            category="daily_summary",
            content=content,
            summary=content,
            importance_score=0.55,
            source_type="summary",
        )

    def _memory_score(self, memory: models.LongTermMemory, query: str) -> float:
        lowered = query.lower()
        haystack = f"{memory.category or ''} {memory.memory_type} {memory.summary or ''} {memory.content}".lower()
        tokens = [token for token in lowered.replace(",", " ").replace("，", " ").split() if len(token) >= 2]
        keyword_hits = sum(1 for token in tokens if token in haystack)
        return float(memory.importance or 0.5) + float(memory.recency_score or 0.5) * 0.15 + keyword_hits * 0.1

    def _category_from_type(self, memory_type: str) -> str:
        mapping = {
            "medical_context": "risk",
            "risk_signal": "risk",
            "stable_preference": "preference",
            "nutrition_habit": "nutrition",
            "training_performance": "training",
            "recent_state": "recovery",
            "correction": "profile",
            "plan_preference": "plan",
        }
        return mapping.get(memory_type, memory_type)

    def _catalog_title(self, category: str) -> str:
        return f"{category.replace('_', ' ').title()} Memory"

    def _catalog_summary(self, category: str, memories: list[models.LongTermMemory]) -> str:
        if not memories:
            return f"No active {category} memories yet."
        summaries = [memory.summary or self._compact_summary(memory.content) for memory in memories[:5]]
        return f"{len(memories)} active {category} memories. Recent: " + " | ".join(summaries)

    def _query_hints(self, category: str) -> list[str]:
        hints = {
            "training": ["progression decision", "exercise history", "workout completion"],
            "nutrition": ["calorie adherence", "protein target", "eating out"],
            "recovery": ["sleep", "fatigue", "soreness"],
            "risk": ["pain", "medical boundary", "training contraindication"],
            "preference": ["likes", "dislikes", "coach style"],
            "plan": ["active plan", "weekly schedule", "adjustment"],
        }
        return hints.get(category, [category])

    def _child_table_for_category(self, category: str) -> str | None:
        mapping = {
            "training": "exercise_logs",
            "nutrition": "nutrition_daily_summaries",
            "recovery": "recovery_logs",
            "risk": "risk_notes",
            "plan": "training_plans",
        }
        return mapping.get(category, "long_term_memories")

    def _compact_summary(self, content: str, max_chars: int = 180) -> str:
        normalized = " ".join(content.split())
        return self._trim(normalized, max_chars)

    def _trim(self, content: str, max_chars: int) -> str:
        if len(content) <= max_chars:
            return content
        return content[: max_chars - 3].rstrip() + "..."
