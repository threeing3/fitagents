"""Owner-scoped pagination over immutable append positions, never execution."""

import uuid

from sqlalchemy import select

from fast_api.app.db import models
from fast_api.app.services.durable_stream_journal import read_stream_journal


def stream_event_page(db, run_id, owner_id, cursor=None, limit=100):
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("Invalid page size")
    run = db.scalar(
        select(models.AgentRun).where(
            models.AgentRun.id == run_id, models.AgentRun.user_id == owner_id
        )
    )
    if run is not None:
        markers = [node for node in (run.nodes or []) if node.get("type") == "DurableStreamJournal"]
        if len(markers) != 1:
            raise LookupError("Recorded stream unavailable")
        try:
            identity = uuid.UUID(str(markers[0].get("journal_id")))
        except (ValueError, TypeError) as exc:
            raise LookupError("Recorded stream unavailable") from exc
        session_id = run.session_id
    else:
        record = db.scalar(
            select(models.IdempotencyRecord).where(
                models.IdempotencyRecord.id == run_id,
                models.IdempotencyRecord.user_id == owner_id,
                models.IdempotencyRecord.operation == "chat",
            )
        )
        if record is None:
            raise LookupError("Recorded stream unavailable")
        try:
            session_id = uuid.UUID(str((record.request_json or {}).get("session_id")))
        except (ValueError, TypeError) as exc:
            raise LookupError("Recorded stream unavailable") from exc
        identity = record.id
    if session_id is not None:
        session = db.scalar(
            select(models.ConversationSession).where(
                models.ConversationSession.id == session_id,
                models.ConversationSession.user_id == owner_id,
            )
        )
        if session is None:
            raise LookupError("Recorded stream unavailable")
    after = 0
    if cursor is not None:
        try:
            source, position = cursor.split(":")
            if source != str(identity) or not position.isascii() or not position.isdigit():
                raise ValueError("Invalid stream cursor")
            after = int(position)
        except (ValueError, AttributeError) as exc:
            raise ValueError("Invalid stream cursor") from exc
    journal = read_stream_journal(identity, owner_id, session_id, after=after, limit=limit)
    if journal is None:
        raise LookupError("Recorded stream unavailable")
    from fast_api.app.services.execution_trace import trace_detail

    return {
        "run_id": str(run_id),
        "journal_id": str(identity),
        "events": [
            {"position": after + index + 1, "event": trace_detail(entry)}
            for index, entry in enumerate(journal["entries"])
        ],
        "next_cursor": f"{identity}:{journal['next_position']}",
        "has_more": journal["has_more"],
        "recorded_count": journal["recorded_count"],
        "status": journal["state"],
        "damaged_tail": journal["damaged_tail"],
        "liveness": "not_checked",
        "may_repeat_writes": False,
    }
