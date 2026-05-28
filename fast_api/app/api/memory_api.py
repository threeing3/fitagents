"""Memory and context API — all endpoints require authentication."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from fast_api.app.core.auth import get_current_user
from fast_api.app.db import models
from fast_api.app.db.database import get_db
from fast_api.app.schemas.agent import (
    AgentDecisionCreate,
    AgentDecisionResponse,
    ContextBuildRequest,
    MemoryItemCreate,
    MemoryItemResponse,
    MemorySearchRequest,
)
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.decision_logger import DecisionLogger
from fast_api.app.services.memory_system import MemoryManager

memory_router = APIRouter()


def _memory_response(memory) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=memory.id,
        user_id=memory.user_id,
        memory_type=memory.memory_type,
        category=memory.category,
        content=memory.content,
        summary=memory.summary,
        importance_score=memory.importance,
        confidence_score=memory.confidence,
        source_type=memory.source,
        metadata=memory.memory_metadata or {},
        created_at=memory.created_at,
    )


def _decision_response(decision) -> AgentDecisionResponse:
    return AgentDecisionResponse(
        id=decision.id,
        user_id=decision.user_id,
        decision_type=decision.decision_type,
        input_summary=decision.input_summary,
        context_used=decision.context_used or {},
        decision_result=decision.decision_result,
        reason=decision.reason,
        confidence_score=decision.confidence_score,
        accepted_by_user=decision.accepted_by_user,
        created_at=decision.created_at,
    )


@memory_router.post("/memory/items", response_model=MemoryItemResponse)
def create_memory_item(
    request: Request,
    payload: MemoryItemCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    manager = MemoryManager(db)
    memory = manager.add_memory(current_user.id, payload.model_dump(exclude={"user_id"}))
    db.commit()
    db.refresh(memory)
    return _memory_response(memory)


@memory_router.get("/memory/items", response_model=list[MemoryItemResponse])
def list_memory_items(
    category: str | None = None,
    memory_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    manager = MemoryManager(db)
    return [
        _memory_response(memory)
        for memory in manager.list_memories(
            user_id=current_user.id,
            category=category,
            memory_type=memory_type,
            limit=limit,
        )
    ]


@memory_router.get("/memory/catalog", response_model=list[dict[str, Any]])
def get_memory_catalog(
    category: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    manager = MemoryManager(db)
    return [
        {
            "id": str(entry.id),
            "category": entry.category,
            "title": entry.title,
            "summary": entry.summary,
            "record_count": entry.record_count,
            "importance_score": entry.importance_score,
            "query_hints": entry.query_hints,
            "child_table": entry.child_table,
            "child_filter": entry.child_filter,
            "last_updated_at": entry.last_updated_at,
        }
        for entry in manager.get_memory_catalog(current_user.id, category=category)
    ]


@memory_router.post("/memory/search", response_model=list[MemoryItemResponse])
def search_memory(
    request: Request,
    payload: MemorySearchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    manager = MemoryManager(db)
    memories = manager.search_memories(
        current_user.id,
        payload.query,
        top_k=payload.top_k,
        category=payload.category,
        memory_type=payload.memory_type,
    )
    db.commit()
    return [_memory_response(memory) for memory in memories]


@memory_router.post("/agent/context", response_model=dict[str, Any])
def build_agent_context(
    request: Request,
    payload: ContextBuildRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    builder = ContextBuilder(db)
    return builder.build_context_packet(current_user.id, payload.user_message, payload.intent)


@memory_router.post("/agent/decision", response_model=AgentDecisionResponse)
def create_agent_decision(
    request: AgentDecisionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    logger = DecisionLogger(db)
    decision = logger.log_decision(
        current_user.id,
        request.model_dump(exclude={"user_id"}),
    )
    db.commit()
    db.refresh(decision)
    return _decision_response(decision)


@memory_router.get("/agent/decisions", response_model=list[AgentDecisionResponse])
def list_agent_decisions(
    decision_type: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    logger = DecisionLogger(db)
    return [
        _decision_response(decision)
        for decision in logger.get_recent_decisions(
            current_user.id,
            decision_type=decision_type,
            limit=limit,
        )
    ]
