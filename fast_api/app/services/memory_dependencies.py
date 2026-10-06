"""Revoke explicitly sourced derived memories, transitively and within one owner."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fast_api.app.db import models


def invalidate_derived_memories(
    db: Session, user_id: uuid.UUID, sources: list[dict[str, str]], reason: str
) -> list[str]:
    db.flush()
    revoked = {(item["table"], str(item["id"])) for item in sources}
    if not revoked:
        return []
    memories = list(
        db.scalars(
            select(models.LongTermMemory).where(
                models.LongTermMemory.user_id == user_id,
                models.LongTermMemory.status == "active",
                models.LongTermMemory.memory_network.in_(["experience", "observation", "opinion"]),
            )
        )
    )
    changed = []
    categories = set()
    while True:
        progress = False
        for memory in memories:
            if memory.status != "active":
                continue
            evidence = {
                (item.get("table"), str(item.get("id")))
                for item in memory.evidence or []
                if isinstance(item, dict)
            }
            if memory.parent_memory_id:
                evidence.add(("long_term_memories", str(memory.parent_memory_id)))
            if not evidence.intersection(revoked):
                continue
            memory.status = "superseded"
            memory.valid_until = datetime.utcnow()
            memory.memory_metadata = {
                **(memory.memory_metadata or {}),
                "source_invalidation": {"reason": reason, "sources": sources},
            }
            revoked.add(("long_term_memories", str(memory.id)))
            changed.append(str(memory.id))
            if memory.category:
                categories.add(memory.category)
            progress = True
        if not progress:
            break
    db.flush()
    if categories:
        from fast_api.app.services.memory_system import MemoryManager

        manager = MemoryManager(db)
        for category in categories:
            manager.update_memory_catalog(user_id, category)
    return changed
