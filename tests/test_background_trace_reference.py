"""References require explicit ownership and matching attempt identity."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fast_api.app.services.background_trace_reference import background_trace_reference


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing",
        "malformed",
        "foreign_owner",
        "foreign_task",
        "old_attempt",
        "wrong_journal",
        "missing_marker",
        "wrong_type",
    ],
)
def test_background_attempt_reference_validation(case):
    identity, owner, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    job = SimpleNamespace(
        id=task_id,
        user_id=owner,
        attempts=1,
        payload_json={"execution_trace_run_id": str(identity)},
    )
    run = SimpleNamespace(
        user_id=owner,
        run_type="background.task",
        nodes=[
            {"type": "BackgroundTaskReference", "task_id": str(task_id), "attempt": 1},
            {"type": "DurableStreamJournal", "journal_id": str(identity)},
        ],
    )
    if case == "missing":
        run = None
    elif case == "malformed":
        job.payload_json["execution_trace_run_id"] = "bad"
    elif case == "foreign_owner":
        run.user_id = uuid.uuid4()
    elif case == "foreign_task":
        run.nodes[0]["task_id"] = str(uuid.uuid4())
    elif case == "old_attempt":
        job.attempts = 2
    elif case == "wrong_journal":
        run.nodes[1]["journal_id"] = str(uuid.uuid4())
    elif case == "missing_marker":
        run.nodes = run.nodes[:1]
    elif case == "wrong_type":
        run.run_type = "chat"
    db = Mock()
    db.get.return_value = run
    assert background_trace_reference(db, job) == (str(identity) if case == "valid" else None)


def test_absent_job_has_no_trace():
    db = Mock()
    assert background_trace_reference(db, None) is None
    db.get.assert_not_called()
