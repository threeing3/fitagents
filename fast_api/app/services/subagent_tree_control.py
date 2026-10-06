"""Durable owner-scoped stop signals for host review trees; no global task map."""

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.subagent_journal import SubagentJournal
from fast_api.app.services.subagent_runtime import SubagentError

OPERATION = "subagent_tree_control"


class TreeStopRequested(SubagentError):
    pass


async def controlled_entries(stream, control):
    watcher = asyncio.create_task(control.wait_for_stop())
    next_item = None
    try:
        while True:
            if watcher.done():
                await watcher
                raise TreeStopRequested("user_cancelled")
            next_item = asyncio.create_task(anext(stream))
            done, _ = await asyncio.wait({next_item, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done and next_item not in done:
                await watcher
                raise TreeStopRequested("user_cancelled")
            try:
                yield await next_item
            except StopAsyncIteration:
                return
    finally:
        tasks = [watcher] + ([next_item] if next_item is not None else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stream.aclose()


def control_record(db, parent_id, owner_id, *, lock=False):
    query = select(models.IdempotencyRecord).where(
        models.IdempotencyRecord.user_id == owner_id,
        models.IdempotencyRecord.operation == OPERATION,
        models.IdempotencyRecord.idempotency_key == str(parent_id),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


class SubagentTreeControl:
    def __init__(self, engine, parent_id, owner_id, session_id):
        self.engine = engine
        self.parent_id = uuid.UUID(str(parent_id))
        self.owner_id = uuid.UUID(str(owner_id))
        self.session_id = uuid.UUID(str(session_id))

    def register(self):
        with Session(self.engine) as db:
            session = db.get(models.ConversationSession, self.session_id)
            if session is None or session.user_id != self.owner_id:
                raise SubagentError("scope_mismatch")
            if control_record(db, self.parent_id, self.owner_id) is not None:
                raise SubagentError("control_already_registered")
            db.add(
                models.IdempotencyRecord(
                    user_id=self.owner_id,
                    operation=OPERATION,
                    idempotency_key=str(self.parent_id),
                    request_json={"session_id": str(self.session_id)},
                    status="processing",
                    response_json={"cancel_requested": False},
                )
            )
            db.commit()

    async def wait_for_stop(self):
        while True:
            with Session(self.engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                row = control_record(db, self.parent_id, self.owner_id)
                if row is None or row.request_json.get("session_id") != str(self.session_id):
                    raise SubagentError("control_missing")
                if row.response_json.get("cancel_requested") is True:
                    return
            await asyncio.sleep(0.25)

    def finish(self, *, stopped):
        with Session(self.engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            row = control_record(db, self.parent_id, self.owner_id, lock=True)
            if row is None:
                raise SubagentError("control_missing")
            row.status = "completed"
            row.completed_at = datetime.utcnow()
            row.response_json = {
                **row.response_json,
                "stopped": stopped,
                "no_automatic_retry": True,
            }
            db.commit()


def request_tree_stop(db, parent_id, owner_id, session_id, expected_revision):
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise SubagentError("invalid_revision")
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    catalog = SubagentJournal(db).load(parent_id, owner_id, session_id)
    if db.get_bind().dialect.name != "postgresql":
        raise SubagentError("independent_journal_backend_unsupported")
    if catalog.get("stop_control") != {"protocol": 1}:
        raise SubagentError("control_not_supported")
    if catalog["revision"] != expected_revision:
        raise SubagentError("checkpoint_conflict")
    row = control_record(db, parent_id, owner_id, lock=True)
    if row is None or row.request_json.get("session_id") != str(session_id):
        raise SubagentError("control_not_supported")
    if row.status == "completed":
        return {"status": "already_recorded", "stopped": row.response_json.get("stopped", False)}
    row.response_json = {**row.response_json, "cancel_requested": True}
    db.commit()
    return {"status": "cancel_requested", "no_automatic_retry": True}
