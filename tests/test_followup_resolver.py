import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fast_api.app.db.database import Base
from fast_api.app.db import models
from fast_api.app.services.followup_resolver import FollowupResolver


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def seed_user_session(db):
    user = models.User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@test.local",
        password_hash="test-hash",
        display_name="Tester",
    )
    session = models.ConversationSession(id=uuid.uuid4(), user_id=user.id)
    db.add(user)
    db.add(session)
    db.flush()
    return user, session


def test_resolves_letter_answer_against_pending_question():
    db = make_db()
    user, session = seed_user_session(db)
    assistant = models.ChatMessage(
        id=uuid.uuid4(),
        user_id=user.id,
        session_id=session.id,
        role="assistant",
        content="你现在的疼痛是哪种？\n- A. 肌肉酸痛\n- B. 关节刺痛\n- C. 肿胀受限",
        message_metadata={},
    )
    db.add(assistant)
    db.flush()

    resolver = FollowupResolver(db)
    pending = resolver.remember_from_assistant_message(user.id, session.id, assistant.id, assistant.content)

    assert pending is not None
    assert pending.question_type == "pain_type_selection"

    user_message_id = uuid.uuid4()
    result = resolver.resolve(user.id, session.id, "A", user_message_id)

    assert result.resolved is True
    assert result.selected_option["key"] == "A"
    assert "肌肉酸痛" in result.normalized_message
    assert pending.status == "answered"
    assert pending.resolved_message_id == user_message_id


def test_no_pending_question_keeps_message_unchanged():
    db = make_db()
    user, session = seed_user_session(db)

    result = FollowupResolver(db).resolve(user.id, session.id, "A")

    assert result.resolved is False
    assert result.normalized_message == "A"
