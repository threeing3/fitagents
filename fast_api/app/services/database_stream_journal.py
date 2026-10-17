"""Transaction-independent, owner-scoped diagnostic events for ephemeral hosts."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from fast_api.app.db.database import SessionLocal
from fast_api.app.db.models import StreamJournal, StreamJournalEvent
from fast_api.app.services.execution_trace import trace_detail


class DatabaseStreamJournal:
    def __init__(self, identity, owner_id, session_id):
        self.identity = uuid.UUID(str(identity))
        self.closed = False
        header = {
            "type": "journal.header",
            "journal_id": str(self.identity),
            "owner_id": str(owner_id),
            "session_id": str(session_id),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with SessionLocal.begin() as db:
                db.add(
                    StreamJournal(
                        id=self.identity,
                        owner_id=str(owner_id),
                        session_id=str(session_id),
                        recorded_count=0,
                        header=header,
                    )
                )
        except IntegrityError as exc:
            raise FileExistsError("Journal identity already recorded") from exc

    def append(self, entry):
        if self.closed:
            raise ValueError("Journal already closed")
        # Match the file backend's redaction and JSON conversion before persistence.
        payload = json.loads(json.dumps(trace_detail(entry), ensure_ascii=False, default=str))
        with SessionLocal.begin() as db:
            journal = db.scalar(
                select(StreamJournal).where(StreamJournal.id == self.identity).with_for_update()
            )
            if journal is None:
                raise LookupError("Journal unavailable")
            journal.recorded_count += 1
            db.add(
                StreamJournalEvent(
                    journal_id=self.identity, position=journal.recorded_count, payload=payload
                )
            )

    def close(self):
        # Closing is not proof of business completion; only journal.end supplies status.
        self.closed = True


def read_database_journal(identity, owner_id, session_id, *, after=0, limit=None):
    identity = uuid.UUID(str(identity))
    with SessionLocal.begin() as db:
        # Freeze the append count during pagination; writers take the same row lock.
        journal = db.scalar(
            select(StreamJournal)
            .where(
                StreamJournal.id == identity,
                StreamJournal.owner_id == str(owner_id),
                StreamJournal.session_id == str(session_id),
            )
            .with_for_update()
        )
        if journal is None:
            return None
        count = journal.recorded_count
        if after > count:
            raise ValueError("Cursor is ahead of recorded journal")
        query = (
            select(StreamJournalEvent)
            .where(
                StreamJournalEvent.journal_id == identity,
                StreamJournalEvent.position > after,
                StreamJournalEvent.position <= count,
            )
            .order_by(StreamJournalEvent.position)
        )
        if limit is not None:
            query = query.limit(limit)
        entries = [row.payload for row in db.scalars(query)]
        terminal = db.scalar(
            select(StreamJournalEvent.payload)
            .where(
                StreamJournalEvent.journal_id == identity,
                StreamJournalEvent.position <= count,
                StreamJournalEvent.payload["type"].as_string() == "journal.end",
            )
            .order_by(StreamJournalEvent.position.desc())
            .limit(1)
        )
        return {
            "journal_id": str(identity),
            "entries": entries,
            "state": terminal.get("state") if terminal else "unconfirmed",
            "damaged_tail": False,
            "liveness": "not_checked",
            "may_repeat_writes": False,
            "next_position": after + len(entries),
            "has_more": after + len(entries) < count,
            "recorded_count": count,
        }
