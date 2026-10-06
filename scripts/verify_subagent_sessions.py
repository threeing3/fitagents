"""Dedicated PostgreSQL business adapter checks with scripted, not real, model."""

import asyncio
import json
import uuid
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.core.errors import IdempotencyConflictError
from fast_api.app.db import models
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.subagent_journal import SubagentJournal
from fast_api.app.services.subagent_sessions import SubagentSessions


async def main():
    engine = create_engine(
        "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"
    )
    owner, session = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        db.add(
            models.User(
                id=owner,
                email=f"child-session-{owner}@example.com",
                password_hash="synthetic-disabled",
            )
        )
        db.flush()
        db.add_all(
            [
                models.ConversationSession(
                    id=session, user_id=owner, title="Synthetic child sessions"
                ),
                models.UserProfile(
                    user_id=owner,
                    age=25,
                    sex="male",
                    height_cm=175,
                    weight_kg=70,
                    goal="maintenance",
                    injuries=[],
                    equipment_available=["dumbbells"],
                ),
            ]
        )
        db.commit()
    provider = ModelProvider(
        Settings(
            _env_file=None,
            LLM_PROVIDER="offline",
            EMBEDDING_PROVIDER="offline",
            ADAPTER_INFERENCE_URL=None,
            USE_PGVECTOR=False,
        )
    )
    calls = 0
    block = False
    entered, release = asyncio.Event(), asyncio.Event()

    class ScriptedModel:
        async def ainvoke(self, messages):
            nonlocal calls
            calls += 1
            if block:
                entered.set()
                await asyncio.wait_for(release.wait(), timeout=20)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "action": "final",
                        "summary": "合成脚本，仅验证链路",
                        "recommendations": [],
                        "uncertainties": ["记录不足"],
                        "evidence_ids": [],
                    },
                    ensure_ascii=False,
                )
            )

    provider.has_live_model = lambda: True
    provider.chat_model = lambda **kwargs: ScriptedModel()
    with Session(engine) as db:
        service = SubagentSessions(db, provider)
        first = await service.execute(
            owner, session, "请给训练建议，不修改计划", "synthetic-start", role="training"
        )
        assert first["status"] == "completed", first
        replay = await service.execute(
            owner, session, "请给训练建议，不修改计划", "synthetic-start", role="training"
        )
        assert replay["idempotent_replay"] and calls == 1
        parent = uuid.UUID(first["parent_id"])
        followup = await service.execute(
            owner,
            session,
            "请补充解释，不修改计划",
            "synthetic-next",
            parent_id=parent,
            expected_catalog_revision=first["catalog_revision"],
        )
        assert followup["status"] == "completed", followup
        assert followup["child_id"] == first["child_id"] and followup["remaining_calls"] == 7
        replay2 = await service.execute(
            owner,
            session,
            "请补充解释，不修改计划",
            "synthetic-next",
            parent_id=parent,
            expected_catalog_revision=first["catalog_revision"],
        )
        assert replay2["idempotent_replay"] and calls == 2
        stale = await service.execute(
            owner,
            session,
            "旧版本请求",
            "synthetic-stale",
            parent_id=parent,
            expected_catalog_revision=first["catalog_revision"],
        )
        assert stale["failure_reason"] == "checkpoint_conflict" and calls == 2
        safety = await service.execute(
            owner, session, "我胸痛而且呼吸困难，继续训练吗", "synthetic-safety", role="training"
        )
        assert safety["status"] == "blocked" and calls == 2
        receipt = service.status(owner, session, "synthetic-next")
        assert (
            receipt["status"] == "recorded" and receipt["result"]["child_id"] == first["child_id"]
        )
        assert (
            db.scalars(
                select(models.TrainingPlan).where(models.TrainingPlan.user_id == owner)
            ).all()
            == []
        )
    block = True

    async def third_turn():
        with Session(engine) as db:
            return await SubagentSessions(db, provider).execute(
                owner,
                session,
                "第三次补充解释",
                "synthetic-race",
                parent_id=parent,
                expected_catalog_revision=followup["catalog_revision"],
            )

    active = asyncio.create_task(third_turn())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        assert calls == 3
        with Session(engine) as db:
            service = SubagentSessions(db, provider)
            assert service.status(owner, session, "synthetic-race")["status"] == "unconfirmed"
            try:
                await service.execute(
                    owner,
                    session,
                    "第三次补充解释",
                    "synthetic-race",
                    parent_id=parent,
                    expected_catalog_revision=followup["catalog_revision"],
                )
            except IdempotencyConflictError:
                db.rollback()
            else:
                raise AssertionError("Processing request was reexecuted")
            competitor = await service.execute(
                owner,
                session,
                "竞争续话请求",
                "synthetic-race-other",
                parent_id=parent,
                expected_catalog_revision=followup["catalog_revision"],
            )
            assert competitor["status"] == "failed" and calls == 3, competitor
    finally:
        release.set()
        completed = await active
    assert completed["status"] == "completed" and completed["activation"] == 3, completed
    with Session(engine) as db:
        replay3 = await SubagentSessions(db, provider).execute(
            owner,
            session,
            "第三次补充解释",
            "synthetic-race",
            parent_id=parent,
            expected_catalog_revision=followup["catalog_revision"],
        )
        assert replay3["idempotent_replay"] and calls == 3
    release.clear()
    entered.clear()

    async def cancellable_turn():
        with Session(engine) as db:
            return await SubagentSessions(db, provider).execute(
                owner, session, "饮食只读咨询", "synthetic-cancel", role="nutrition"
            )

    cancellable = asyncio.create_task(cancellable_turn())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        with Session(engine) as db:
            service = SubagentSessions(db, provider)
            assert (
                service.request_cancel(owner, session, "synthetic-cancel")["status"]
                == "cancel_requested"
            )
            assert (
                service.request_cancel(owner, session, "synthetic-cancel")["status"]
                == "cancel_requested"
            )
        cancelled = await asyncio.wait_for(cancellable, timeout=10)
        assert cancelled["failure_reason"] == "user_cancelled", cancelled
        assert cancelled["status"] == "failed" and cancelled["model_called"] is True
        assert calls == 4
        with Session(engine) as db:
            service = SubagentSessions(db, provider)
            assert service.status(owner, session, "synthetic-cancel")["result"] == cancelled
            catalog = SubagentJournal(db).load(uuid.UUID(cancelled["parent_id"]), owner, session)
            assert catalog["children"][0]["status"] == "failed"
            assert catalog["children"][0]["failure_reason"] == "parent_cancelled"
            assert catalog["_continuation"]["phase"] == "unconfirmed"
            assert (
                service.request_cancel(owner, session, "synthetic-cancel")["status"]
                == "already_recorded"
            )
            retry = await service.execute(
                owner, session, "饮食只读咨询", "synthetic-cancel", role="nutrition"
            )
            assert retry["idempotent_replay"] and calls == 4
    finally:
        release.set()
        if not cancellable.done():
            cancellable.cancel()
        try:
            await cancellable
        except asyncio.CancelledError:
            pass
    print(
        json.dumps(
            {
                "status": "passed",
                "start_and_continue_same_identity": True,
                "duplicate_requests_no_model_calls": True,
                "stale_revision_refused": True,
                "safety_host_gate": True,
                "no_plan_writes": True,
                "processing_duplicate_refused": True,
                "competing_parent_request_no_model_calls": True,
                "durable_user_cancel_and_replay": True,
                "scripted_calls": calls,
                "real_model_called": False,
                "session_id": str(session),
                "parent_id": str(parent),
            }
        )
    )
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
