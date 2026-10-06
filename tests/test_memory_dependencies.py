import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fast_api.app.db import models
from fast_api.app.db.database import Base
from fast_api.app.services.memory_dependencies import invalidate_derived_memories


def test_recursive_sources_do_not_revoke_other_users_or_unrelated_memories():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner, other = uuid.uuid4(), uuid.uuid4()
        source = uuid.uuid4()

        def memory(user, network, evidence):
            row = models.LongTermMemory(
                user_id=user,
                memory_type=network,
                memory_network=network,
                category="training",
                content="synthetic",
                evidence=evidence,
                status="active",
            )
            db.add(row)
            db.flush()
            return row

        first = memory(owner, "observation", [{"table": "recovery_logs", "id": str(source)}])
        second = memory(owner, "opinion", [{"table": "long_term_memories", "id": str(first.id)}])
        third = memory(owner, "experience", [{"table": "long_term_memories", "id": str(second.id)}])
        unrelated = memory(owner, "opinion", [{"table": "recovery_logs", "id": str(uuid.uuid4())}])
        foreign = memory(other, "opinion", [{"table": "recovery_logs", "id": str(source)}])
        revoked = invalidate_derived_memories(
            db, owner, [{"table": "recovery_logs", "id": str(source)}], "corrected"
        )
        assert set(revoked) == {str(first.id), str(second.id), str(third.id)}
        assert unrelated.status == foreign.status == "active"
        assert first.evidence == [{"table": "recovery_logs", "id": str(source)}]
        assert (
            invalidate_derived_memories(
                db, owner, [{"table": "recovery_logs", "id": str(source)}], "again"
            )
            == []
        )
    engine.dispose()
