import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db.database import Base
from fast_api.app.services.background_tasks import BackgroundTaskQueue


def test_write_job_failure_never_requeues_and_owner_scope_ignores_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner, other = uuid.uuid4(), uuid.uuid4()
        queue = BackgroundTaskQueue(db)
        historical = queue.enqueue(other, "plan.generate", {"force": True}, max_attempts=9)
        current = queue.enqueue(owner, "plan.generate", {}, max_attempts=9)
        assert current.max_attempts == historical.max_attempts == 1
        selected = queue.claim_next(user_id=owner)
        assert selected.id == current.id and selected.attempts == 1
        queue.mark_failure(selected, "response unavailable after possible commit", 0.1)
        assert selected.status == "outcome_unknown"
        assert queue.claim_next(user_id=owner) is None
        db.refresh(historical)
        assert historical.status == "queued" and historical.attempts == 0
    engine.dispose()
