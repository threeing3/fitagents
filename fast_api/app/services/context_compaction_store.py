"""Durable lease/CAS checkpoint storage independent of business transactions."""

import copy
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from fast_api.app.db import models
from fast_api.app.services.model_call_records import record_context_compaction


class CompactionBusy(ValueError):
    pass


class ContextCompactionStore:
    def __init__(self, bind, scope_id, owner_id, session_id):
        self.factory = sessionmaker(bind=bind, expire_on_commit=False)
        self.scope_id = scope_id
        self.owner_id = str(owner_id)
        self.session_id = str(session_id)
        self.revision = None
        self.active_id = None

    def read(self):
        with self.factory() as db:
            row = db.get(models.ContextCheckpointState, self.scope_id)
            if row is None:
                return 0, {}
            self._authorize(row)
            return row.revision, copy.deepcopy(row.checkpoint)

    def _authorize(self, row):
        if row.owner_id != self.owner_id or row.session_id != self.session_id:
            raise PermissionError("Context scope does not belong to this owner/session")

    def _event(self, db, event):
        db.add(
            models.ContextCompactionEvent(
                scope_id=self.scope_id,
                owner_id=self.owner_id,
                session_id=self.session_id,
                compaction_id=event["compaction_id"],
                event_type=event["type"],
                payload=event,
            )
        )

    async def emit(self, event):
        now = datetime.utcnow()
        if event["type"] == "compaction/start":
            try:
                with self.factory.begin() as db:
                    if db.get(models.ContextCheckpointState, self.scope_id) is None:
                        db.add(
                            models.ContextCheckpointState(
                                scope_id=self.scope_id,
                                owner_id=self.owner_id,
                                session_id=self.session_id,
                                revision=0,
                                checkpoint={},
                            )
                        )
            except IntegrityError:
                pass  # Another caller initialized the same scope; authorize under lock below.
        with self.factory.begin() as db:
            row = db.scalar(
                select(models.ContextCheckpointState)
                .where(models.ContextCheckpointState.scope_id == self.scope_id)
                .with_for_update()
            )
            if row is None:
                raise LookupError("Missing compaction scope")
            self._authorize(row)
            if event["type"] == "compaction/start":
                if row.active_compaction_id:
                    lease = row.lease_until
                    if lease is not None and lease.replace(tzinfo=None) > now:
                        raise CompactionBusy("A durable compaction lease is active")
                    self._event(
                        db,
                        {
                            "type": "compaction/end",
                            "compaction_id": row.active_compaction_id,
                            "status": "outcome_unknown",
                            "reason": "expired_lease",
                            "installed": row.checkpoint.get("id") == row.active_compaction_id,
                        },
                    )
                if self.revision is not None and self.revision != row.revision:
                    raise CompactionBusy("Surface revision changed before compaction")
                self.revision = row.revision
                self.active_id = event["compaction_id"]
                row.active_compaction_id = self.active_id
                row.lease_until = now + timedelta(seconds=180)
            elif row.active_compaction_id != event["compaction_id"]:
                raise CompactionBusy("Compaction lease was superseded")
            if event["type"] == "compaction/end":
                row.active_compaction_id = None
                row.lease_until = None
            self._event(db, event)
        # The database is authoritative; a journal failure must not undo a committed event.
        record_context_compaction(event)

    async def install(self, ids, checkpoint, *, source_rows=None):
        with self.factory.begin() as db:
            if source_rows is not None:
                source_ids = [uuid.UUID(item["id"]) for item in source_rows]
                rows = list(
                    db.scalars(
                        select(models.ChatMessage)
                        .where(
                            models.ChatMessage.id.in_(source_ids),
                            models.ChatMessage.user_id == uuid.UUID(self.owner_id),
                            models.ChatMessage.session_id == uuid.UUID(self.session_id),
                        )
                        .with_for_update()
                    )
                )
                actual = {str(row.id): (row.role, row.content) for row in rows}
                expected = {item["id"]: (item["role"], item["content"]) for item in source_rows}
                if actual != expected:
                    raise CompactionBusy("Original source messages changed before installation")
            result = db.execute(
                update(models.ContextCheckpointState)
                .where(
                    models.ContextCheckpointState.scope_id == self.scope_id,
                    models.ContextCheckpointState.owner_id == self.owner_id,
                    models.ContextCheckpointState.session_id == self.session_id,
                    models.ContextCheckpointState.revision == self.revision,
                    models.ContextCheckpointState.active_compaction_id == self.active_id,
                    models.ContextCheckpointState.lease_until > datetime.utcnow(),
                )
                .values(checkpoint=checkpoint, revision=self.revision + 1)
            )
            if result.rowcount != 1:
                raise CompactionBusy("Checkpoint compare-and-swap failed")
            self._event(
                db,
                {
                    "type": "compaction/installed",
                    "compaction_id": self.active_id,
                    "source_message_ids": ids,
                    "revision": self.revision + 1,
                },
            )


def conversation_checkpoint(db, user_id, session_id):
    if session_id is None:
        return {}
    row = db.get(models.ContextCheckpointState, "conversation:" + str(session_id))
    if row is None:
        return {}
    if row.owner_id != str(user_id) or row.session_id != str(session_id):
        raise PermissionError("Checkpoint owner mismatch")
    return copy.deepcopy(row.checkpoint)
