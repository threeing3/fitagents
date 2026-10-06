"""Synthetic-only PostgreSQL isolation probe. No DDL, migrations or deletions."""

import asyncio
import json
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fast_api.app.core.config import Settings
from fast_api.app.db import models
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.domain_subagents import DomainSubagents, project_read
from fast_api.app.services.model_provider import ModelProvider
from fast_api.app.services.subagent_journal import RUN_TYPE, JournalCheckpoint, SubagentJournal
from fast_api.app.services.subagent_tree_control import request_tree_stop


async def verify():
    engine = create_engine(
        "postgresql+psycopg://fitagent_test@127.0.0.1:15432/fitagent_acceptance_20261002"
    )
    owner, session = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        assert db.scalar(select(models.User).limit(1)) is not None
        db.add(
            models.User(
                id=owner,
                email=f"checkpoint-{owner}@example.test",
                password_hash="disabled-synthetic",
            )
        )
        db.flush()
        db.add(
            models.ConversationSession(
                id=session, user_id=owner, title="Synthetic checkpoint probe"
            )
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
    pending_goal = uuid.uuid4()
    with Session(engine) as host:
        host.add(
            models.FitnessGoal(
                id=pending_goal, user_id=owner, goal_type="synthetic-probe", target="must rollback"
            )
        )
        host.flush()
        service = CoachAgentService(host, provider)
        stream = service._domain_collaboration(
            {"intent": "weekly_review"}, "Synthetic review; no plan writes", owner, session
        )
        first = await anext(stream)
        parent = uuid.UUID(first["details"]["parent_id"])
        with Session(engine) as observer:
            saved = SubagentJournal(observer).load(parent, owner, session)
            assert saved["children"][0]["status"] == "pending"
            assert observer.get(models.FitnessGoal, pending_goal) is None
        await stream.aclose()
        host.rollback()
    with Session(engine) as observer:
        saved = SubagentJournal(observer).load(parent, owner, session)
        assert saved["children"][0]["status"] == "failed"
        assert saved["children"][0]["failure_reason"] == "consumer_closed"
        assert observer.get(models.FitnessGoal, pending_goal) is None
    with Session(engine) as host:
        packet = {"intent": "weekly_review"}
        service = CoachAgentService(host, provider)
        events = [
            entry
            async for entry in service._domain_collaboration(
                packet, "Synthetic review", owner, session
            )
        ]
        assert len(packet["domain_consultations"]) == 5
        assert all(row["status"] == "skipped" for row in packet["domain_consultations"])
        parent2 = uuid.UUID(events[0]["details"]["parent_id"])
    with Session(engine) as observer:
        complete = SubagentJournal(observer).load(parent2, owner, session)
        assert len(complete["children"]) == 5
        assert observer.get(models.AgentRun, parent2).run_type == RUN_TYPE
        assert observer.get(models.AgentRun, parent2).status == "skipped"
    entered, cancelled = asyncio.Event(), asyncio.Event()
    scripted_calls = 0

    class BlockedModel:
        async def ainvoke(self, _messages):
            nonlocal scripted_calls
            scripted_calls += 1
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    provider.has_live_model = lambda: True
    provider.chat_model = lambda **_kwargs: BlockedModel()
    worker = DomainSubagents(provider, lambda role, tool: project_read({}, role, tool))
    worker.runtime.bind(str(owner), str(session))
    worker.runtime.checkpoint = JournalCheckpoint(engine)

    async def drain():
        return [
            entry
            async for entry in worker.run({"intent": "training_plan"}, "Synthetic cancellation")
        ]

    task = asyncio.create_task(drain())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert cancelled.is_set()
    worker.runtime.checkpoint.close()
    parent3 = uuid.UUID(worker.runtime.parent_id)
    with Session(engine) as observer:
        interrupted = SubagentJournal(observer).load(parent3, owner, session)
        assert interrupted["children"][0]["failure_reason"] == "parent_cancelled"
        assert observer.get(models.AgentRun, parent3).status == "failed"
    entered.clear()
    cancelled.clear()
    packet4 = {"intent": "weekly_review"}

    async def host_review():
        with Session(engine) as host:
            service = CoachAgentService(host, provider)
            return [
                entry
                async for entry in service._domain_collaboration(
                    packet4, "Synthetic review stop", owner, session
                )
            ]

    host_task = asyncio.create_task(host_review())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        with Session(engine) as observer:
            catalogs = SubagentJournal(observer).list_catalogs(owner, session)
            current = next(
                row
                for row in catalogs
                if row.get("stop_control") == {"protocol": 1}
                and any(child["status"] == "running" for child in row["children"])
            )
            parent4 = uuid.UUID(current["parent_id"])
            response = request_tree_stop(observer, parent4, owner, session, current["revision"])
            assert response["status"] == "cancel_requested"
        events4 = await asyncio.wait_for(host_task, timeout=10)
        assert cancelled.is_set() and scripted_calls == 2
        assert packet4["review_collaboration_status"] == "failed"
        assert packet4["domain_consultations"] == []
        assert any(
            event.get("details", {}).get("failure_reason") == "user_cancelled" for event in events4
        )
        with Session(engine) as observer:
            stopped = SubagentJournal(observer).load(parent4, owner, session)
            assert len(stopped["children"]) == 1
            assert stopped["children"][0]["failure_reason"] == "parent_cancelled"
    finally:
        if not host_task.done():
            host_task.cancel()
            try:
                await host_task
            except asyncio.CancelledError:
                pass
    print(
        json.dumps(
            {
                "status": "passed",
                "synthetic_owner": str(owner),
                "synthetic_session": str(session),
                "interrupted_catalog": str(parent),
                "offline_review_catalog": str(parent2),
                "independent_visibility": True,
                "host_write_rolled_back": True,
                "explicit_close_persisted": True,
                "offline_roles": 5,
                "model_called": False,
                "scripted_inflight_cancel_persisted": True,
                "cancelled_catalog": str(parent3),
                "host_tree_stop_no_downstream_calls": True,
                "stopped_review_catalog": str(parent4),
            },
            ensure_ascii=False,
        )
    )
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
