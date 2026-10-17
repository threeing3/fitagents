"""Bounded live acceptance on a retained backup clone, never on a real account."""

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scripts.run_local_preview import configure_local


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", required=True)
    args = parser.parse_args()
    if (
        not args.clone.startswith("fitagent_compaction_verify_")
        or not args.clone.replace("_", "").isalnum()
    ):
        raise ValueError("Use a verified disposable clone name")
    configure_local()
    from fast_api.app.core.config import get_settings
    from fast_api.app.db import models
    from fast_api.app.services.model_provider import ModelProvider
    from fast_api.app.services.session_compaction import prepare_session_context, session_surface

    settings = get_settings()
    from sqlalchemy.engine import make_url

    engine = create_engine(make_url(settings.database_url).set(database=args.clone))
    owner, session_id = uuid.uuid4(), uuid.uuid4()

    class AcceptanceProvider(ModelProvider):
        async def summarize_context(self, request):
            try:
                response = await super().summarize_context(request)
                print(
                    json.dumps({"synthetic_summary_output": response}, ensure_ascii=False),
                    flush=True,
                )
                return response
            except Exception as error:
                print(
                    {
                        "summarizer_error": type(error).__name__,
                        "code": getattr(error, "code", None),
                    },
                    flush=True,
                )
                raise

    with Session(engine) as db:
        db.add(
            models.User(
                id=owner, email=str(owner) + "@compaction.test", password_hash="synthetic-not-login"
            )
        )
        db.add(models.ConversationSession(id=session_id, user_id=owner))
        for index, text in enumerate(
            [
                "我周三休息，只做力量训练，不跑步。",
                "已记录周三休息和力量训练限制，未执行任何计划修改。",
                "纠正：我周四也不能训练；不要自动修改计划。",
                "已记录纠正：周三和周四休息。没有修改计划。",
            ]
        ):
            db.add(
                models.ChatMessage(
                    id=uuid.uuid4(),
                    user_id=owner,
                    session_id=session_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=text + "\n背景补充：" + "synthetic archived context. " * 100,
                    created_at=datetime(2026, 10, 1) + timedelta(minutes=index),
                )
            )
        db.commit()
        original = session_surface(db, owner, session_id)
        result = await prepare_session_context(
            db,
            AcceptanceProvider(settings, user_id=owner, endpoint="compaction_acceptance"),
            owner,
            session_id,
            "只整理已确认事实，不要创建计划。",
            manual=True,
        )
        surface = session_surface(db, owner, session_id)
        checkpoints = [item for item in surface if item.get("context_checkpoint")]
        events = list(
            db.scalars(
                select(models.ContextCompactionEvent).where(
                    models.ContextCompactionEvent.owner_id == str(owner)
                )
            )
        )
        raw = list(
            db.scalars(
                select(models.ChatMessage)
                .where(models.ChatMessage.session_id == session_id)
                .order_by(models.ChatMessage.created_at, models.ChatMessage.id)
            )
        )
        assert [row.content for row in raw] == [item["content"] for item in original]
        print(
            {
                "compaction_status": result.get("status"),
                "reason": result.get("reason"),
                "event_types": [event.event_type for event in events],
            },
            flush=True,
        )
        assert result["status"] == "completed" and checkpoints
        summary = checkpoints[0]["content"]
        assert "周三" in summary and "周四" in summary
        report = {
            "status": result["status"],
            "synthetic_owner": str(owner),
            "source_rows_preserved": len(raw),
            "events": [event.event_type for event in events],
            "checkpoint": checkpoints[0],
            "clone": args.clone,
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
