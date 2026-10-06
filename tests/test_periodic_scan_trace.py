"""Aggregate system logs do not publish multi-user scan contents."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fast_api.app.services import background_tasks as tasks
from fast_api.app.services import durable_stream_journal as journals


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    return tmp_path


def read_only_log(log_dir):
    paths = list((log_dir / "streams").glob("*.jsonl"))
    assert len(paths) == 1
    identity = uuid.UUID(paths[0].stem)
    return journals.read_stream_journal(identity, "background-worker", None)


@pytest.mark.parametrize("count", [0, 2])
def test_effect_scan_commit_semantics_and_private_result_omission(log_dir, monkeypatch, count):
    from fast_api.app.services.decision_evaluation import DecisionEvaluationService

    result = {
        "processed": count,
        "results": [{"user_id": "private-user", "answer": "private-health"}],
    }
    scan = Mock(return_value=result)
    monkeypatch.setattr(DecisionEvaluationService, "scan_due", scan)
    db = Mock()
    assert tasks.run_due_decision_evaluations(db) is result
    assert db.commit.call_count == (1 if count else 0)
    scan.assert_called_once_with()
    log = read_only_log(log_dir)
    assert log["state"] == "completed"
    assert "private" not in str(log)
    assert any(item.get("counts") == {"processed": count} for item in log["entries"])


def test_responsibility_scan_preserves_unconditional_commit(log_dir, monkeypatch):
    from fast_api.app.services.responsibilities import ResponsibilityService

    monkeypatch.setattr(ResponsibilityService, "scan_due", lambda self: {"queued": 0, "expired": 0})
    db = Mock()
    assert tasks.run_due_responsibilities(db) == {"queued": 0, "expired": 0}
    db.commit.assert_called_once_with()
    assert read_only_log(log_dir)["state"] == "completed"


@pytest.mark.parametrize("error", [ValueError("private error"), asyncio.CancelledError()])
def test_scan_error_and_cancel_keep_start_without_retry(log_dir, error):
    db = Mock()
    scan = Mock(side_effect=error)
    with pytest.raises(type(error)):
        tasks._run_periodic_scan(db, "local.scan", scan, "processed")
    scan.assert_called_once_with()
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()
    log = read_only_log(log_dir)
    assert log["state"] == "interrupted"
    assert log["entries"][0]["type"] == "scan.start"
    assert "private error" not in str(log)


def test_commit_error_is_unknown_not_false_rollback_receipt(log_dir):
    db = Mock()
    db.commit.side_effect = OSError("private connection")
    with pytest.raises(OSError):
        tasks._run_periodic_scan(db, "local.scan", lambda: {"processed": 1}, "processed")
    log = read_only_log(log_dir)
    assert log["state"] == "interrupted"
    assert log["entries"][-1]["business_commit_verification"] == "unconfirmed"
    assert not any(item["type"] == "scan.commit_returned" for item in log["entries"])


def test_start_persistence_failure_blocks_scan(log_dir, monkeypatch):
    original = journals.DurableStreamJournal.append

    def fail_start(self, item):
        if item.get("type") == "scan.start":
            raise OSError("disk unavailable")
        return original(self, item)

    monkeypatch.setattr(journals.DurableStreamJournal, "append", fail_start)
    scan = Mock()
    with pytest.raises(OSError):
        tasks._run_periodic_scan(Mock(), "local.scan", scan, "processed")
    scan.assert_not_called()


def test_rollback_failure_does_not_replace_original_cancel(log_dir):
    db = Mock()
    db.rollback.side_effect = OSError("private rollback connection")
    scan = Mock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        tasks._run_periodic_scan(db, "local.scan", scan, "processed")
    log = read_only_log(log_dir)
    assert log["entries"][-1]["error_type"] == "CancelledError"
    assert "private rollback connection" not in str(log)


def test_post_commit_log_failure_cannot_prove_business_rollback(log_dir, monkeypatch):
    original = journals.DurableStreamJournal.append

    def fail_after_commit(self, item):
        if item.get("type") == "scan.commit_returned":
            raise OSError("local recording failure")
        return original(self, item)

    monkeypatch.setattr(journals.DurableStreamJournal, "append", fail_after_commit)
    db = Mock()
    scan = Mock(return_value={"processed": 1})
    with pytest.raises(OSError):
        tasks._run_periodic_scan(db, "local.scan", scan, "processed")
    db.commit.assert_called_once_with()
    scan.assert_called_once_with()
    log = read_only_log(log_dir)
    assert log["state"] == "interrupted"
    assert log["entries"][-1]["business_commit_verification"] == "unconfirmed"
