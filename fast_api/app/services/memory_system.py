import uuid
from datetime import date, datetime
import re
from typing import Any

from sqlalchemy import desc, func, literal, or_, select
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.bm25 import build_weighted_document, rank_by_bm25
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
        content = payload["content"]
        if self.is_correction_message(content) and not payload.get("skip_correction_flow"):
            result = self.handle_correction_flow(
                user_id=user_id,
                message=content,
                category=payload.get("category"),
            )
            if result.get("memory") is not None:
                return result["memory"]
        return self.create_memory_item(
            user_id=user_id,
            memory_type=payload.get("memory_type", "episodic"),
            memory_network=payload.get("memory_network", "world"),
            fact_kind=payload.get("fact_kind", "unknown"),
            category=payload.get("category"),
            content=content,
            summary=payload.get("summary"),
            importance_score=float(payload.get("importance_score", payload.get("importance", 0.6))),
            confidence_score=float(payload.get("confidence_score", payload.get("confidence", 0.75))),
            source_type=payload.get("source_type", payload.get("source", "manual")),
            source_id=payload.get("source_id"),
            metadata=payload.get("metadata") or payload.get("memory_metadata") or {},
            occurred_start=payload.get("occurred_start"),
            occurred_end=payload.get("occurred_end"),
            mentioned_at=payload.get("mentioned_at"),
            entities=payload.get("entities") or [],
            evidence=payload.get("evidence") or [],
        )

    def create_memory_item(
        self,
        user_id: uuid.UUID,
        memory_type: str,
        category: str | None,
        content: str,
        summary: str | None = None,
        memory_network: str = "world",
        fact_kind: str = "unknown",
        importance_score: float = 0.6,
        confidence_score: float = 0.75,
        source_type: str = "manual",
        source_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_start: datetime | None = None,
        occurred_end: datetime | None = None,
        mentioned_at: datetime | None = None,
        entities: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> models.LongTermMemory:
        memory_category = category or self._category_from_type(memory_type)
        memory_summary = summary or self._compact_summary(content)
        extracted_entities = self.extract_entities(content)
        merged_entities = self._merge_entities(entities or [], extracted_entities)
        embedding_text = "\n".join(
            [
                memory_network,
                fact_kind,
                memory_category or "",
                memory_summary or "",
                content,
                " ".join(entity.get("canonical", "") for entity in merged_entities),
            ]
        )
        memory = models.LongTermMemory(
            user_id=user_id,
            memory_type=memory_type,
            memory_network=memory_network,
            fact_kind=fact_kind,
            category=memory_category,
            content=content,
            summary=memory_summary,
            importance=importance_score,
            recency_score=1.0,
            confidence=confidence_score,
            source=source_type,
            occurred_start=occurred_start,
            occurred_end=occurred_end,
            mentioned_at=mentioned_at or datetime.utcnow(),
            entities=merged_entities,
            evidence=evidence or [],
            memory_metadata={
                **(metadata or {}),
                **({"source_id": str(source_id)} if source_id else {}),
            },
            embedding=self.model_provider.embed_text(embedding_text),
        )
        self.db.add(memory)
        self.db.flush()
        self.update_memory_catalog(user_id, memory.category or memory_type)
        self.update_memory_blocks(user_id)
        return memory

    def retain_memory(
        self,
        user_id: uuid.UUID,
        content: str,
        memory_network: str,
        fact_kind: str,
        category: str | None = None,
        summary: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        occurred_start: datetime | None = None,
        occurred_end: datetime | None = None,
        importance_score: float = 0.6,
        confidence_score: float = 0.75,
        source_type: str = "system",
    ) -> models.LongTermMemory:
        if memory_network == "opinion" and not evidence:
            raise ValueError("opinion memory requires evidence")
        return self.create_memory_item(
            user_id=user_id,
            memory_type=fact_kind,
            memory_network=memory_network,
            fact_kind=fact_kind,
            category=category or self._category_from_fact_kind(fact_kind),
            content=content,
            summary=summary or self._compact_summary(content),
            importance_score=importance_score,
            confidence_score=confidence_score,
            source_type=source_type,
            entities=entities or [],
            evidence=evidence or [],
            occurred_start=occurred_start,
            occurred_end=occurred_end,
        )

    def retain_agent_decision_as_experience(
        self,
        user_id: uuid.UUID,
        decision: models.AgentDecision,
    ) -> models.LongTermMemory:
        content = (
            f"Agent decision {decision.decision_type}: {decision.decision_result}. "
            f"Reason: {decision.reason}"
        )
        return self.retain_memory(
            user_id=user_id,
            content=content,
            memory_network="experience",
            fact_kind="agent_action",
            category="decision",
            summary=self._compact_summary(content),
            evidence=[{"table": "agent_decisions", "id": str(decision.id)}],
            importance_score=0.65,
            confidence_score=float(decision.confidence_score or 0.75),
            source_type="agent_decision",
        )

    def is_correction_message(self, text: str) -> bool:
        signals = ["不对", "不是", "改了", "现在不是", "已经好了", "医生说", "更正", "纠正", "changed", "not anymore"]
        lowered = (text or "").lower()
        return any(signal in lowered for signal in signals)

    def handle_correction_flow(
        self,
        user_id: uuid.UUID,
        message: str,
        category: str | None = None,
        link_type: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_correction_message(message):
            return {"correction_detected": False, "memory": None, "updated_memories": [], "links": []}
        old_memories = self.search_memories(user_id, message, top_k=5, category=category, include_expired=False)
        new_memory = self.retain_memory(
            user_id=user_id,
            content=message,
            memory_network="world",
            fact_kind="correction",
            category=category or self._infer_correction_category(message),
            evidence=[{
                "table": "user_messages",
                "id": "current",
                "summary": self._compact_summary(message, 120),
                "time": datetime.utcnow().isoformat(),
            }],
            importance_score=0.78,
            confidence_score=0.85,
            source_type="correction",
        )
        now = datetime.utcnow()
        links: list[models.MemoryLink] = []
        inferred_link_type = link_type or self._infer_memory_link_type(message)
        for old_memory in old_memories:
            if old_memory.id == new_memory.id:
                continue
            old_memory.status = "superseded"
            old_memory.valid_until = now
            link = models.MemoryLink(
                user_id=user_id,
                source_memory_id=new_memory.id,
                target_memory_id=old_memory.id,
                link_type=inferred_link_type,
                reason=self._compact_summary(message, 180),
                link_metadata={"correction_signal": True},
            )
            self.db.add(link)
            links.append(link)
            if old_memory.category:
                self.update_memory_catalog(user_id, old_memory.category)
        self.update_memory_catalog(user_id, new_memory.category or "correction")
        self.update_memory_blocks(user_id)
        self.db.flush()
        return {
            "correction_detected": True,
            "memory": new_memory,
            "updated_memories": old_memories,
            "links": links,
        }

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
        memory_network: str | None = None,
        fact_kind: str | None = None,
        entities: list[str] | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        include_expired: bool = False,
    ) -> list[models.LongTermMemory]:
        filters = [
            models.LongTermMemory.user_id == user_id,
        ]
        if not include_expired:
            filters.append(models.LongTermMemory.status == "active")
        if category:
            filters.append(models.LongTermMemory.category == category)
        if memory_type:
            filters.append(models.LongTermMemory.memory_type == memory_type)
        if memory_network:
            filters.append(models.LongTermMemory.memory_network == memory_network)
        if fact_kind:
            filters.append(models.LongTermMemory.fact_kind == fact_kind)
        if occurred_after:
            filters.append(models.LongTermMemory.occurred_start >= occurred_after)
        if occurred_before:
            filters.append(models.LongTermMemory.occurred_start <= occurred_before)
        if not include_expired:
            filters.append(
                or_(
                    models.LongTermMemory.valid_until.is_(None),
                    models.LongTermMemory.valid_until >= datetime.utcnow(),
                )
            )

        vector_candidates = self._semantic_candidates(query, filters, top_k)

        query_entities = self.extract_entities(query)
        wanted_entities = set(entities or [])
        wanted_entities.update(entity["canonical"] for entity in query_entities)
        entity_candidates: list[models.LongTermMemory] = []
        if wanted_entities:
            entity_candidates = list(
                self.db.scalars(
                    select(models.LongTermMemory)
                    .where(*filters)
                    .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                    .limit(top_k * 8)
                )
            )
            entity_candidates = [
                memory for memory in entity_candidates if self._memory_has_entity(memory, wanted_entities)
            ]

        keyword_candidates, keyword_signal_scores = self._keyword_candidates(query, filters, top_k)

        temporal_candidates = list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(*filters)
                .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                .limit(top_k * 3)
            )
        )
        vector_rank = {memory.id: index for index, memory in enumerate(vector_candidates)}
        entity_rank = {memory.id: index for index, memory in enumerate(entity_candidates)}
        temporal_rank = {memory.id: index for index, memory in enumerate(temporal_candidates)}
        by_id = {memory.id: memory for memory in vector_candidates}
        for memory in [*entity_candidates, *keyword_candidates, *temporal_candidates]:
            by_id.setdefault(memory.id, memory)
        candidates = list(by_id.values())
        bm25_matches = rank_by_bm25(candidates, query, self._memory_bm25_document)
        bm25_scores = {
            match.item.id: max(match.normalized_score, keyword_signal_scores.get(match.item.id, 0.0))
            for match in bm25_matches
        }
        keyword_matches = sorted(
            [match for match in bm25_matches if bm25_scores.get(match.item.id, 0.0) > 0],
            key=lambda match: bm25_scores.get(match.item.id, 0.0),
            reverse=True,
        )
        keyword_rank = {match.item.id: index for index, match in enumerate(keyword_matches)}
        ranked = sorted(
            candidates,
            key=lambda item: self._memory_score(
                item,
                bm25_scores.get(item.id, 0.0),
                vector_rank,
                keyword_rank,
                entity_rank,
                temporal_rank,
            ),
            reverse=True,
        )[:top_k]
        for memory in ranked:
            final_score = self._memory_score(
                memory,
                bm25_scores.get(memory.id, 0.0),
                vector_rank,
                keyword_rank,
                entity_rank,
                temporal_rank,
            )
            self._attach_search_debug(
                memory,
                vector_rank,
                keyword_rank,
                entity_rank,
                temporal_rank,
                final_score,
                bm25_scores.get(memory.id, 0.0),
            )
            self.mark_memory_accessed(memory)
        return ranked

    def _semantic_candidates(
        self,
        query: str,
        filters: list[Any],
        top_k: int,
    ) -> list[models.LongTermMemory]:
        try:
            query_embedding = self.model_provider.embed_text(query)
            return list(
                self.db.scalars(
                    select(models.LongTermMemory)
                    .where(*filters, models.LongTermMemory.embedding.is_not(None))
                    .order_by(models.LongTermMemory.embedding.cosine_distance(query_embedding))
                    .limit(top_k * 3)
                )
            )
        except Exception:
            return []

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

    def _memory_score(
        self,
        memory: models.LongTermMemory,
        bm25_score: float,
        vector_rank: dict[uuid.UUID, int] | None = None,
        keyword_rank: dict[uuid.UUID, int] | None = None,
        entity_rank: dict[uuid.UUID, int] | None = None,
        temporal_rank: dict[uuid.UUID, int] | None = None,
    ) -> float:
        rrf_score = 0.0
        if vector_rank and memory.id in vector_rank:
            rrf_score += 1.0 / (60.0 + vector_rank[memory.id])
        if keyword_rank and memory.id in keyword_rank:
            rrf_score += 1.25 / (60.0 + keyword_rank[memory.id])
        if entity_rank and memory.id in entity_rank:
            rrf_score += 1.5 / (60.0 + entity_rank[memory.id])
        if temporal_rank and memory.id in temporal_rank:
            rrf_score += 0.75 / (60.0 + temporal_rank[memory.id])
        importance = float(memory.importance or 0.5)
        recency = float(memory.recency_score or 0.5)
        risk_priority = 0.22 if self._is_risk_or_health_memory(memory) else 0.0
        opinion_penalty = -0.05 if getattr(memory, "memory_network", "world") == "opinion" else 0.0
        return rrf_score + bm25_score * 0.45 + importance * 0.25 + recency * 0.1 + risk_priority + opinion_penalty

    def _keyword_candidates(
        self,
        query: str,
        filters: list[Any],
        top_k: int,
    ) -> tuple[list[models.LongTermMemory], dict[uuid.UUID, float]]:
        candidates: list[models.LongTermMemory] = []
        signal_scores: dict[uuid.UUID, float] = {}
        if self._is_postgres():
            try:
                tsvector = func.to_tsvector("simple", self._keyword_search_document_expr())
                tsquery = func.plainto_tsquery("simple", query)
                rank = func.ts_rank_cd(tsvector, tsquery).label("keyword_rank_score")
                rows = list(
                    self.db.execute(
                        select(models.LongTermMemory, rank)
                        .where(*filters, tsvector.op("@@")(tsquery))
                        .order_by(desc(rank), desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                        .limit(top_k * 8)
                    )
                )
                for memory, score in rows:
                    candidates.append(memory)
                    signal_scores[memory.id] = max(signal_scores.get(memory.id, 0.0), float(score or 0.0))
            except Exception:
                candidates = []

        fallback_candidates = self._keyword_fallback_candidates(query, filters, top_k)
        for memory, score in fallback_candidates:
            if memory.id not in {item.id for item in candidates}:
                candidates.append(memory)
            signal_scores[memory.id] = max(signal_scores.get(memory.id, 0.0), score)
        return candidates, signal_scores

    def _keyword_search_document_expr(self):
        parts = [
            models.LongTermMemory.summary,
            models.LongTermMemory.content,
            models.LongTermMemory.category,
            models.LongTermMemory.memory_type,
            models.LongTermMemory.memory_network,
            models.LongTermMemory.fact_kind,
        ]
        expr = literal("")
        for part in parts:
            expr = expr + literal(" ") + func.coalesce(part, "")
        return expr

    def _keyword_fallback_candidates(
        self,
        query: str,
        filters: list[Any],
        top_k: int,
    ) -> list[tuple[models.LongTermMemory, float]]:
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.append(models.LongTermMemory.summary.ilike(pattern))
            clauses.append(models.LongTermMemory.content.ilike(pattern))
            clauses.append(models.LongTermMemory.category.ilike(pattern))
            clauses.append(models.LongTermMemory.memory_type.ilike(pattern))
            clauses.append(models.LongTermMemory.fact_kind.ilike(pattern))
        rows = list(
            self.db.scalars(
                select(models.LongTermMemory)
                .where(*filters, or_(*clauses))
                .order_by(desc(models.LongTermMemory.importance), desc(models.LongTermMemory.created_at))
                .limit(top_k * 12)
            )
        )
        scored = [(memory, self._token_overlap_score(query, self._memory_bm25_document(memory))) for memory in rows]
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def _query_tokens(self, text: str) -> list[str]:
        tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", text or "")]
        tokens.extend(entity["canonical"].lower() for entity in self.extract_entities(text or ""))
        seen: set[str] = set()
        unique: list[str] = []
        for token in tokens:
            if len(token) < 2 and not re.search(r"[\u4e00-\u9fff]", token):
                continue
            if token not in seen:
                unique.append(token)
                seen.add(token)
        return unique[:12]

    def _token_overlap_score(self, query: str, document: str) -> float:
        query_tokens = set(self._query_tokens(query))
        if not query_tokens:
            return 0.0
        doc_lower = (document or "").lower()
        hits = sum(1 for token in query_tokens if token in doc_lower)
        return hits / len(query_tokens)

    def _attach_search_debug(
        self,
        memory: models.LongTermMemory,
        vector_rank: dict[uuid.UUID, int],
        keyword_rank: dict[uuid.UUID, int],
        entity_rank: dict[uuid.UUID, int],
        temporal_rank: dict[uuid.UUID, int],
        final_score: float,
        keyword_score: float = 0.0,
    ) -> None:
        semantic_rank = self._rank_value(memory.id, vector_rank)
        keyword_rank_value = self._rank_value(memory.id, keyword_rank)
        entity_rank_value = self._rank_value(memory.id, entity_rank)
        temporal_rank_value = self._rank_value(memory.id, temporal_rank)
        memory.semantic_rank = semantic_rank
        memory.keyword_rank = keyword_rank_value
        memory.entity_rank = entity_rank_value
        memory.temporal_rank = temporal_rank_value
        memory.final_score = round(final_score, 6)
        sources = []
        if semantic_rank is not None:
            sources.append("semantic")
        if keyword_rank_value is not None:
            sources.append("keyword")
        if entity_rank_value is not None:
            sources.append("entity")
        if temporal_rank_value is not None:
            sources.append("temporal")
        memory.retrieval_debug = {
            "sources": sources,
            "semantic_rank": semantic_rank,
            "keyword_rank": keyword_rank_value,
            "entity_rank": entity_rank_value,
            "temporal_rank": temporal_rank_value,
            "keyword_score": round(float(keyword_score or 0.0), 6),
            "risk_priority": 0.22 if self._is_risk_or_health_memory(memory) else 0.0,
            "importance": float(memory.importance or 0.0),
            "recency": float(memory.recency_score or 0.0),
            "final_score": memory.final_score,
        }

    def _rank_value(self, memory_id: uuid.UUID, ranks: dict[uuid.UUID, int]) -> int | None:
        if memory_id not in ranks:
            return None
        return ranks[memory_id] + 1

    def _is_postgres(self) -> bool:
        bind = self.db.get_bind()
        return bool(bind and bind.dialect.name == "postgresql")

    def _memory_bm25_document(self, memory: models.LongTermMemory) -> str:
        metadata = memory.memory_metadata or {}
        metadata_text = " ".join(str(value) for value in metadata.values() if value is not None)
        entity_text = " ".join(
            f"{entity.get('name', '')} {entity.get('canonical', '')}"
            for entity in (getattr(memory, "entities", None) or [])
        )
        return build_weighted_document(
            [
                (getattr(memory, "memory_network", "world"), 2),
                (getattr(memory, "fact_kind", "unknown"), 2),
                (memory.category, 2),
                (memory.memory_type, 2),
                (memory.summary, 3),
                (memory.content, 4),
                (entity_text, 3),
                (metadata_text, 1),
            ]
        )

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

    def _category_from_fact_kind(self, fact_kind: str) -> str:
        mapping = {
            "user_profile_fact": "profile",
            "health_fact": "risk",
            "workout_event": "training",
            "nutrition_event": "nutrition",
            "recovery_event": "recovery",
            "symptom_event": "risk",
            "preference": "preference",
            "correction": "profile",
            "agent_action": "decision",
            "coach_observation": "observation",
            "coach_opinion": "opinion",
            "daily_summary": "daily_summary",
            "weekly_summary": "weekly_summary",
        }
        return mapping.get(fact_kind, "profile")

    def _infer_correction_category(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(term in lowered for term in ["减脂", "增肌", "目标", "goal", "fat loss", "muscle gain"]):
            return "profile"
        if any(term in lowered for term in ["好了", "疼", "痛", "医生", "风险", "risk", "pain", "resolved"]):
            return "risk"
        if any(term in lowered for term in ["吃", "饮食", "素食", "外卖", "nutrition", "diet"]):
            return "nutrition"
        return "profile"

    def _infer_memory_link_type(self, text: str) -> str:
        lowered = (text or "").lower()
        if any(term in lowered for term in ["不对", "不是", "现在不是", "not anymore"]):
            return "contradicts"
        return "updates"

    def extract_entities(self, text: str) -> list[dict[str, str]]:
        lowered = text.lower()
        patterns: list[tuple[str, str, str, list[str]]] = [
            ("exercise", "卧推", "bench_press", ["卧推", "bench"]),
            ("exercise", "深蹲", "squat", ["深蹲", "squat"]),
            ("exercise", "硬拉", "deadlift", ["硬拉", "deadlift"]),
            ("exercise", "引体", "pull_up", ["引体", "pull-up", "pullup"]),
            ("symptom", "胸闷", "chest_tightness", ["胸闷", "chest tightness"]),
            ("symptom", "头晕", "dizziness", ["头晕", "dizzy"]),
            ("symptom", "疼痛", "pain", ["疼痛", "疼", "pain"]),
            ("symptom", "刺痛", "sharp_pain", ["刺痛", "sharp pain"]),
            ("symptom", "麻木", "numbness", ["麻木", "numb"]),
            ("symptom", "呼吸困难", "breathing_difficulty", ["呼吸困难", "breathing difficulty"]),
            ("symptom", "酸痛", "soreness", ["酸痛", "soreness"]),
            ("condition", "甲亢", "hyperthyroidism", ["甲亢", "hyperthyroid"]),
            ("condition", "甲状腺", "thyroid", ["甲状腺", "thyroid"]),
            ("medication", "赛治", "methimazole", ["赛治"]),
            ("medication", "甲巯咪唑", "methimazole", ["甲巯咪唑", "methimazole"]),
            ("nutrition", "蛋白粉", "protein_powder", ["蛋白粉", "protein powder"]),
            ("nutrition", "鱼油", "fish_oil", ["鱼油", "fish oil"]),
            ("nutrition", "香蕉", "banana", ["香蕉", "banana"]),
            ("nutrition", "外卖", "takeout", ["外卖", "外食", "takeout"]),
            ("nutrition", "海鲜", "seafood", ["海鲜", "seafood"]),
            ("recovery", "睡眠", "sleep", ["睡眠", "睡觉", "sleep"]),
            ("recovery", "疲劳", "fatigue", ["疲劳", "累", "fatigue", "tired"]),
            ("recovery", "压力", "stress", ["压力", "stress"]),
            ("recovery", "心率", "heart_rate", ["心率", "heart rate"]),
            ("goal", "增肌", "muscle_gain", ["增肌", "muscle gain"]),
            ("goal", "减脂", "fat_loss", ["减脂", "fat loss"]),
            ("goal", "力量", "strength", ["力量", "strength"]),
            ("goal", "恢复", "recovery", ["恢复", "recovery"]),
        ]
        entities: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for entity_type, name, canonical, aliases in patterns:
            if any(alias.lower() in lowered for alias in aliases):
                key = (entity_type, canonical)
                if key not in seen:
                    entities.append({"type": entity_type, "name": name, "canonical": canonical})
                    seen.add(key)
        return entities

    def _merge_entities(
        self,
        explicit_entities: list[dict[str, Any]],
        extracted_entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for entity in [*explicit_entities, *extracted_entities]:
            entity_type = str(entity.get("type") or "")
            canonical = str(entity.get("canonical") or entity.get("name") or "")
            if not entity_type or not canonical:
                continue
            key = (entity_type, canonical)
            if key in seen:
                continue
            merged.append({
                "type": entity_type,
                "name": str(entity.get("name") or canonical),
                "canonical": canonical,
            })
            seen.add(key)
        return merged

    def _memory_has_entity(self, memory: models.LongTermMemory, wanted_entities: set[str]) -> bool:
        for entity in memory.entities or []:
            if entity.get("canonical") in wanted_entities or entity.get("name") in wanted_entities:
                return True
        return False

    def _is_risk_or_health_memory(self, memory: models.LongTermMemory) -> bool:
        return (
            memory.category == "risk"
            or memory.memory_type in {"medical_context", "risk_signal", "symptom_event", "health_fact", "medication"}
            or getattr(memory, "fact_kind", "") in {"health_fact", "symptom_event", "medication_event", "medication"}
            or any((entity.get("type") == "medication") for entity in (getattr(memory, "entities", None) or []))
        )

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
