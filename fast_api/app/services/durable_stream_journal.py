"""Append-only public stream evidence independent of business transactions."""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fast_api.app.core.config import get_settings


def journal_path(identity, log_dir=None):
    # Only canonical UUIDs select paths; never use user text or idempotency keys.
    return (
        Path(log_dir or get_settings().agent_log_dir)
        / "streams"
        / f"{uuid.UUID(str(identity))}.jsonl"
    )


class DurableStreamJournal:
    def __init__(self, identity, owner_id, session_id, log_dir=None):
        self.identity = str(uuid.UUID(str(identity)))
        self.closed = False
        self.database_journal = None
        if (
            log_dir is None
            and getattr(get_settings(), "stream_journal_backend", "file") == "database"
        ):
            from fast_api.app.services.database_stream_journal import DatabaseStreamJournal

            self.database_journal = DatabaseStreamJournal(identity, owner_id, session_id)
            return
        self.path = journal_path(identity, log_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("x", encoding="utf-8")
        self.closed = False
        try:
            self.append(
                {
                    "type": "journal.header",
                    "journal_id": self.identity,
                    "owner_id": str(owner_id),
                    "session_id": str(session_id),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except BaseException:
            self.file.close()
            raise

    def append(self, entry):
        if self.database_journal is not None:
            return self.database_journal.append(entry)
        from fast_api.app.services.execution_trace import trace_detail

        if self.closed:
            raise ValueError("Journal already closed")
        self.file.write(json.dumps(trace_detail(entry), ensure_ascii=False, default=str) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        if self.database_journal is not None:
            self.closed = True
            return self.database_journal.close()
        if not self.closed:
            self.closed = True
            self.file.close()


def read_stream_journal(identity, owner_id, session_id, log_dir=None, *, after=0, limit=None):
    if log_dir is None and getattr(get_settings(), "stream_journal_backend", "file") == "database":
        from fast_api.app.services.database_stream_journal import read_database_journal

        return read_database_journal(identity, owner_id, session_id, after=after, limit=limit)
    path = journal_path(identity, log_dir)
    if not path.is_file():
        return None
    entries = []
    damaged_tail = False
    header_valid = False
    count = 0
    terminal = None
    with path.open("rb") as file:
        for index, raw in enumerate(file):
            # A writer may still be completing this line. Never advance over it.
            if not raw.endswith(b"\n"):
                if index == 0:
                    return None
                damaged_tail = True
                break
            try:
                item = json.loads(raw)
            except ValueError:
                if index == 0:
                    return None
                damaged_tail = True
                break
            if not isinstance(item, dict):
                if index == 0:
                    return None
                damaged_tail = True
                break
            if index == 0:
                if (
                    item.get("type") != "journal.header"
                    or item.get("journal_id") != str(identity)
                    or item.get("owner_id") != str(owner_id)
                    or item.get("session_id") != str(session_id)
                ):
                    return None
                header_valid = True
            else:
                count += 1
                if item.get("type") == "journal.end":
                    terminal = item
                if count > after and (limit is None or len(entries) < limit):
                    entries.append(item)
    if not header_valid:
        return None
    if after > count:
        raise ValueError("Cursor is ahead of recorded journal")
    return {
        "journal_id": str(identity),
        "entries": entries,
        "state": terminal.get("state") if terminal and not damaged_tail else "unconfirmed",
        "damaged_tail": damaged_tail,
        "liveness": "not_checked",
        "may_repeat_writes": False,
        "next_position": after + len(entries),
        "has_more": after + len(entries) < count,
        "recorded_count": count,
    }
