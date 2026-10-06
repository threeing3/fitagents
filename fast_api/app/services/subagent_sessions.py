"""Explicit read-only child sessions with request receipts, never inferred writes."""

import asyncio
import uuid
from contextlib import suppress
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.context_builder import ContextBuilder
from fast_api.app.services.domain_subagents import ROLE_CONFIG, project_read
from fast_api.app.services.subagent_continuation import ContinuableChild
from fast_api.app.services.subagent_journal import JournalCheckpoint, SubagentJournal, lease_key
from fast_api.app.services.subagent_runtime import SubagentError

OPERATION = "subagent_turn"
ROLES = {"training", "nutrition", "recovery", "evidence_analysis", "plan_planning"}


class SubagentSessions:
    def __init__(self, db, provider):
        self.db, self.provider = db, provider

    def scope(self, owner_id, session_id):
        session = self.db.scalar(
            select(models.ConversationSession).where(
                models.ConversationSession.id == session_id,
                models.ConversationSession.user_id == owner_id,
            )
        )
        if session is None:
            raise SubagentError("scope_mismatch")

    def status(self, owner_id, session_id, key):
        self.scope(owner_id, session_id)
        record = self.db.scalar(
            select(models.IdempotencyRecord).where(
                models.IdempotencyRecord.user_id == owner_id,
                models.IdempotencyRecord.operation == OPERATION,
                models.IdempotencyRecord.idempotency_key == key.strip(),
            )
        )
        if record is None or record.request_json.get("session_id") != str(session_id):
            return {"status": "not_found"}
        if record.status == "completed":
            return {"status": "recorded", "result": record.response_json}
        return {
            "status": "unconfirmed",
            "parent_id": (record.response_json or {}).get("parent_id"),
            "no_automatic_retry": True,
        }

    def request_cancel(self, owner_id, session_id, key):
        self.scope(owner_id, session_id)
        if self.db.get_bind().dialect.name != "postgresql":
            raise SubagentError("independent_journal_backend_unsupported")
        self.db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
        self.db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        record = self.db.scalar(
            select(models.IdempotencyRecord)
            .where(
                models.IdempotencyRecord.user_id == owner_id,
                models.IdempotencyRecord.operation == OPERATION,
                models.IdempotencyRecord.idempotency_key == key.strip(),
            )
            .with_for_update()
        )
        if record is None or record.request_json.get("session_id") != str(session_id):
            raise SubagentError("request_not_found")
        if record.status == "completed":
            return {"status": "already_recorded", "no_automatic_retry": True}
        record.response_json = {**(record.response_json or {}), "cancel_requested": True}
        self.db.commit()
        return {"status": "cancel_requested", "no_automatic_retry": True}

    def reconcile_request(self, owner_id, session_id, key, expected_revision):
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise SubagentError("invalid_revision")
        self.scope(owner_id, session_id)
        if self.db.get_bind().dialect.name != "postgresql":
            raise SubagentError("independent_journal_backend_unsupported")
        self.db.execute(text("SET LOCAL lock_timeout = '2000ms'"))
        self.db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        record = self.db.scalar(
            select(models.IdempotencyRecord)
            .where(
                models.IdempotencyRecord.user_id == owner_id,
                models.IdempotencyRecord.operation == OPERATION,
                models.IdempotencyRecord.idempotency_key == key.strip(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None or record.request_json.get("session_id") != str(session_id):
            raise SubagentError("request_not_found")
        if record.status == "completed":
            return {"status": "recorded", "result": record.response_json}
        parent = uuid.UUID((record.response_json or {}).get("parent_id", ""))
        if not self.db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lease_key(parent)}
        ):
            raise SubagentError("execution_busy")
        catalog = SubagentJournal(self.db).load(parent, owner_id, session_id)
        if catalog.get("execution_lease") != {"protocol": 1}:
            raise SubagentError("unsupported_liveness_evidence")
        if catalog["revision"] != expected_revision:
            raise SubagentError("checkpoint_conflict")
        children = catalog["children"]
        if not children or any(child["status"] in {"pending", "running"} for child in children):
            raise SubagentError("catalog_not_terminal")
        if any(child["status"] == "completed" for child in children):
            raise SubagentError("result_delivery_unconfirmed")
        result = {
            "status": "failed",
            "failure_reason": "execution_interrupted",
            "parent_id": str(parent),
            "catalog_revision": catalog["revision"],
            "no_business_writes": True,
            "no_automatic_retry": True,
            "model_called": None,
            "reconciled_receipt": True,
        }
        record.response_json = result
        record.status = "completed"
        record.completed_at = datetime.utcnow()
        self.db.commit()
        return {"status": "recorded", "result": result}

    async def execute(
        self,
        owner_id,
        session_id,
        message,
        key,
        *,
        role=None,
        parent_id=None,
        expected_catalog_revision=None,
    ):
        self.scope(owner_id, session_id)
        engine = self.db.get_bind()
        if engine.dialect.name != "postgresql":
            raise SubagentError("independent_journal_backend_unsupported")
        if parent_id is None and role not in ROLES:
            raise SubagentError("invalid_roles")
        request = {
            "session_id": str(session_id),
            "message": message,
            "role": role,
            "parent_id": str(parent_id) if parent_id is not None else None,
            "expected_catalog_revision": expected_catalog_revision,
        }
        record, replay = CoachAgentService(self.db, self.provider)._begin_idempotent_operation(
            owner_id,
            operation=OPERATION,
            idempotency_key=key,
            request_json=request,
        )
        if replay is not None:
            return replay
        if record is None:
            raise SubagentError("request_key_required")
        target_parent = str(parent_id or uuid.uuid4())
        record.response_json = {"parent_id": target_parent}
        self.db.commit()  # This endpoint owns only its request receipt, not business writes.

        def reader_factory(current_message):
            def reader(domain, tool):
                with Session(engine) as reader_db:
                    reader_db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                    packet = ContextBuilder(reader_db, self.provider).build_context_packet(
                        owner_id,
                        current_message,
                        session_id=session_id,
                        intent_decision={"primary_intent": ROLE_CONFIG[domain]["intent"]},
                    )
                    return project_read(packet, domain, tool)

            return reader

        checkpoint = None
        result = {"status": "failed", "parent_id": target_parent, "no_business_writes": True}
        try:
            host = CoachAgentService(self.db, self.provider)
            if host._requires_immediate_safety_reply(message):
                result.update(
                    status="blocked",
                    model_called=False,
                    advice={"summary": host._safety_reply(message)},
                )
                raise SubagentError("safety_review_required")
            if parent_id is None:
                child = ContinuableChild(
                    self.provider,
                    reader_factory,
                    owner_id=owner_id,
                    session_id=session_id,
                    role=role,
                )
                child.worker.runtime.parent_id = target_parent
                catalog_revision, child_revision = 0, None
            else:
                child, catalog_revision = ContinuableChild.restore(
                    self.provider,
                    reader_factory,
                    db=self.db,
                    parent_id=parent_id,
                    owner_id=owner_id,
                    session_id=session_id,
                )
                if expected_catalog_revision != catalog_revision:
                    raise SubagentError("checkpoint_conflict")
                child_revision = child.worker.runtime.children[child.child_id]["revision"]
            checkpoint = JournalCheckpoint(engine)
            checkpoint.revision = catalog_revision
            child.attach_checkpoint(checkpoint)
            checkpoint._ensure_lease(child.worker.runtime)
            self.db.refresh(record)
            if record.status == "completed":
                return {**record.response_json, "idempotent_replay": True}
            if (record.response_json or {}).get("cancel_requested") is True:
                result["model_called"] = False
                raise SubagentError("user_cancelled")
            events, final = [], None
            stream = child.run(owner_id, session_id, message, expected_revision=child_revision)

            async def consume():
                nonlocal final
                async for entry in stream:
                    if entry["type"] == "domain_result":
                        final = entry["results"][0]
                    else:
                        events.append(entry)

            async def cancellation_requested():
                while True:
                    with Session(engine) as control_db:
                        control_db.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                        receipt = control_db.get(models.IdempotencyRecord, record.id)
                        if receipt is None:
                            raise SubagentError("request_receipt_missing")
                        if (receipt.response_json or {}).get("cancel_requested") is True:
                            return
                    await asyncio.sleep(0.25)

            consumer = asyncio.create_task(consume())
            control = asyncio.create_task(cancellation_requested())
            initial_budget = child.worker.remaining_calls
            try:
                done, _ = await asyncio.wait(
                    {consumer, control}, return_when=asyncio.FIRST_COMPLETED
                )
                if control in done and consumer not in done:
                    await control
                    consumer.cancel()
                    with suppress(asyncio.CancelledError):
                        await consumer
                    result.update(
                        model_called=child.worker.remaining_calls < initial_budget,
                        remaining_calls=child.worker.remaining_calls,
                        catalog_revision=checkpoint.revision,
                        child_id=child.child_id,
                    )
                    raise SubagentError("user_cancelled")
                await consumer
            finally:
                consumer.cancel()
                control.cancel()
                for task in (consumer, control):
                    with suppress(asyncio.CancelledError):
                        await task
                await stream.aclose()
            if final is None:
                raise SubagentError("continuation_not_ready")
            result.update(
                status=final["status"],
                child_id=final["child_id"],
                activation=final["activation"],
                catalog_revision=checkpoint.revision,
                remaining_calls=child.worker.remaining_calls,
                execution_events=events,
                advice={
                    key: final[key]
                    for key in ("summary", "recommendations", "uncertainties", "evidence_ids")
                    if key in final
                },
                model_called=final["model_called"],
            )
        except SubagentError as exc:
            result["failure_reason"] = str(exc)
        except Exception:
            result["failure_reason"] = "execution_failed"
        finally:
            if checkpoint is not None:
                checkpoint.close()
        self.db.refresh(record, with_for_update=True)
        if record.status == "completed":
            return {**record.response_json, "idempotent_replay": True}
        record.status = "completed"
        record.response_json = result
        record.completed_at = datetime.utcnow()
        self.db.commit()
        return result
