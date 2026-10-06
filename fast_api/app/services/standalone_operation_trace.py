"""Durable diagnostic metadata without committing the caller's business session."""

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fast_api.app.db import models
from fast_api.app.services.durable_stream_journal import DurableStreamJournal
from fast_api.app.services.model_call_records import model_origin, model_recording

logger = logging.getLogger(__name__)


class StandaloneOperationTrace:
    def __init__(self, db, owner_id, operation):
        engine = db.get_bind()
        # A shared connection could commit caller state. Fail before model transport.
        if not isinstance(engine, Engine) or (
            engine.dialect.name == "sqlite"
            and (
                engine.url.database in (None, "", ":memory:") or isinstance(engine.pool, StaticPool)
            )
        ):
            raise RuntimeError("Independent trace transaction unavailable")
        self.engine = engine
        self.owner_id = owner_id
        self.operation = operation
        self.id = uuid.uuid4()
        self.journal = DurableStreamJournal(self.id, owner_id, None)
        try:
            with Session(engine) as metadata:
                if metadata.get(models.User, owner_id) is None:
                    raise LookupError("Trace owner unavailable")
                metadata.add(
                    models.AgentRun(
                        id=self.id,
                        user_id=owner_id,
                        session_id=None,
                        run_type=operation,
                        status="running",
                        nodes=[{"type": "DurableStreamJournal", "journal_id": str(self.id)}],
                    )
                )
                metadata.commit()
            self.journal.append({"type": "operation.start", "operation": operation})
        except BaseException:
            self.journal.close()
            raise

    def finish(self, state, error_type=None):
        with Session(self.engine) as metadata:
            run = metadata.get(models.AgentRun, self.id)
            if run is None or run.user_id != self.owner_id:
                raise LookupError("Trace owner unavailable")
            run.status = state
            run.completed_at = datetime.now(timezone.utc)
            run.error = error_type
            metadata.commit()
        self.journal.append(
            {
                "type": "journal.end",
                "state": state,
                "error_type": error_type,
                "business_write": False,
                "may_repeat_writes": False,
            }
        )

    @contextmanager
    def recording(self):
        try:
            with model_recording(self.journal), model_origin({"step_id": str(self.id)}):
                yield self
        except BaseException as exc:
            # Preserve the original exception/cancellation even if terminal storage fails.
            state = "failed" if isinstance(exc, Exception) else "interrupted"
            try:
                self.finish(state, type(exc).__name__)
            except Exception as recording_error:
                logger.warning("Trace terminal unavailable: %s", type(recording_error).__name__)
            raise
        finally:
            self.journal.close()
