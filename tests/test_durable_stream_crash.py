"""Actual process termination and persistence failures, without live business data."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_api.app.services import durable_stream_journal as journals
from fast_api.app.services.coach_agent import CoachAgentService


@pytest.mark.parametrize("phase,expected_rows", [("before_commit", 0), ("after_commit", 1)])
def test_killed_process_preserves_events_not_assumed_business_completion(
    tmp_path, phase, expected_rows
):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    worker = Path(__file__).parent / "fixtures" / "durable_journal_worker.py"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(worker),
            str(tmp_path),
            str(identity),
            str(owner),
            str(session),
            phase,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        assert process.poll() is None
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    saved = journals.read_stream_journal(identity, owner, session, str(tmp_path))
    assert saved["state"] == "unconfirmed"
    assert saved["liveness"] == "not_checked"
    assert saved["may_repeat_writes"] is False
    assert saved["damaged_tail"] is False
    assert saved["entries"][0]["event_id"] == "write-request"
    assert not any(entry["type"] == "journal.end" for entry in saved["entries"])
    with sqlite3.connect(tmp_path / "business.sqlite") as database:
        assert database.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == expected_rows
    assert any(entry.get("event_id") == "write-committed" for entry in saved["entries"]) == bool(
        expected_rows
    )


@pytest.mark.parametrize("bad_content", ["", "null\n", "[]\n", "{\n"])
def test_invalid_header_is_not_authoritative_evidence(tmp_path, bad_content):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    path = journals.journal_path(identity, str(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(bad_content, encoding="utf-8")
    assert journals.read_stream_journal(identity, owner, session, str(tmp_path)) is None


def test_damaged_suffix_cannot_claim_completed_journal(tmp_path):
    identity, owner, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    journal = journals.DurableStreamJournal(identity, owner, session, str(tmp_path))
    journal.append({"type": "journal.end", "state": "completed"})
    journal.close()
    with journals.journal_path(identity, str(tmp_path)).open("a", encoding="utf-8") as file:
        file.write("null\n")
    result = journals.read_stream_journal(identity, owner, session, str(tmp_path))
    assert result["state"] == "unconfirmed"
    assert result["damaged_tail"] is True


def test_disk_sync_failure_prevents_public_event_delivery_and_next_action(tmp_path, monkeypatch):
    monkeypatch.setattr(
        journals, "get_settings", lambda: SimpleNamespace(agent_log_dir=str(tmp_path))
    )
    service = object.__new__(CoachAgentService)
    monkeypatch.setattr(service, "_begin_chat_request", lambda *args: (None, None))
    actions = []
    original_sync = journals.os.fsync
    sync_count = 0

    def fail_second_sync(fd):
        nonlocal sync_count
        sync_count += 1
        if sync_count >= 2:
            raise OSError("synthetic disk failure")
        original_sync(fd)

    monkeypatch.setattr(journals.os, "fsync", fail_second_sync)

    async def stream(*args):
        yield json.dumps({"type": "step", "name": "before_tool", "status": "running"}) + "\n"
        actions.append("tool")

    monkeypatch.setattr(service, "_stream_chat_events_once", stream)

    async def collect():
        return [raw async for raw in service.stream_chat_events(uuid.uuid4(), uuid.uuid4(), "test")]

    with pytest.raises(OSError, match="synthetic disk failure"):
        asyncio.run(collect())
    assert actions == []
    assert service._active_chat_request is None
