import asyncio
import json
from datetime import datetime, timedelta

from fast_api.app.db import models
from fast_api.app.services.coach_agent import CoachAgentService
from fast_api.app.services.followup_resolver import FollowupResolver
from tests.test_followup_resolver import make_db, seed_user_session


def pending_measurements(db, user, session, source="22岁 178 74 想增肌 训练一年 可以去健身房"):
    db.add(models.ChatMessage(user_id=user.id, session_id=session.id, role="user", content=source))
    db.flush()
    reply = models.ChatMessage(
        user_id=user.id,
        session_id=session.id,
        role="assistant",
        content="身高：**178 cm**，体重：**74 kg**。对吗？确认一下这两个数字。",
    )
    db.add(reply)
    db.flush()
    return FollowupResolver(db).remember_from_assistant_message(
        user.id, session.id, reply.id, reply.content
    )


def test_confirmation_extracts_supported_measurements():
    db = make_db()
    user, session = seed_user_session(db)
    pending = pending_measurements(db, user, session)
    assert pending.question_type == "profile_measurements"
    result = FollowupResolver(db).resolve(user.id, session.id, "对的")
    assert result.resolved
    agent = CoachAgentService(db)
    patch = agent.extract_profile_updates(result.normalized_message)
    assert patch["height_cm"] == 178
    assert patch["weight_kg"] == 74
    assert pending.answer_json["profile_candidate"]["height_cm"] == 178
    assert not FollowupResolver(db).resolve(user.id, session.id, "对的").resolved


def test_candidate_is_not_assistant_invention_or_other_person():
    for source in ("22岁 想增肌", "我朋友22岁 178 74 想增肌", "22岁 180 80 想增肌"):
        db = make_db()
        user, session = seed_user_session(db)
        pending = pending_measurements(db, user, session, source)
        assert pending.question_type != "profile_measurements"


def test_expiry_session_isolation_and_correction():
    db = make_db()
    user, session = seed_user_session(db)
    pending = pending_measurements(db, user, session)
    _, other = seed_user_session(db)
    assert not FollowupResolver(db).resolve(user.id, other.id, "对的").resolved
    result = FollowupResolver(db).resolve(user.id, session.id, "不对，身高180cm体重75kg")
    assert not result.resolved
    assert pending.status == "cancelled"
    pending = pending_measurements(db, user, session)
    pending.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.flush()
    assert not FollowupResolver(db).resolve(user.id, session.id, "对的").resolved


def test_onboarding_history_is_session_scoped():
    db = make_db()
    user, session = seed_user_session(db)
    pending_measurements(db, user, session)
    agent = CoachAgentService(db)
    history = agent._onboarding_history(user.id, session.id)
    assert len(history) == 2
    assert "178" in history[0]["content"]
    assert agent._onboarding_history(user.id, None) == []


def test_confirmed_candidate_reaches_registry_and_persistent_profile(monkeypatch):
    db = make_db()
    user, session = seed_user_session(db)
    pending_measurements(db, user, session)
    resolution = FollowupResolver(db).resolve(user.id, session.id, "对的")
    agent = CoachAgentService(db)
    monkeypatch.setattr(agent.model_provider, "has_live_model", lambda: False)
    profile = agent._get_or_create_profile(user.id)
    registry = agent._build_chat_tool_registry(
        user.id,
        session.id,
        profile,
        "对的",
        effective_message=resolution.normalized_message,
        followup_resolution=resolution.to_dict(),
    )
    extraction = asyncio.run(registry._handlers["profile.extract"]({}))
    agent._apply_profile_extraction(profile, extraction)
    db.commit()
    db.expire_all()
    persisted = db.get(models.UserProfile, user.id)
    assert persisted.height_cm == 178
    assert persisted.weight_kg == 74


def test_both_onboarding_model_paths_receive_history(monkeypatch):
    db = make_db()
    user, session = seed_user_session(db)
    pending_measurements(db, user, session)
    agent = CoachAgentService(db)
    profile = agent._get_or_create_profile(user.id)
    prompts = []
    monkeypatch.setattr(agent.model_provider, "has_live_model", lambda: True)

    async def reply(system, prompt):
        prompts.append(json.loads(prompt))
        return "请确认"

    async def stream(system, prompt):
        prompts.append(json.loads(prompt))
        yield "请确认"

    monkeypatch.setattr(agent.model_provider, "coach_reply", reply)
    monkeypatch.setattr(agent.model_provider, "stream_coach_reply", stream)
    asyncio.run(agent._live_onboarding_reply(profile, ["height_cm"], "对的", session_id=session.id))

    async def collect():
        return [
            chunk
            async for chunk in agent._live_onboarding_reply_stream(
                profile, ["height_cm"], "对的", session_id=session.id
            )
        ]

    asyncio.run(collect())
    assert len(prompts) == 2
    assert all("178" in p["recent_conversation"][0]["content"] for p in prompts)
