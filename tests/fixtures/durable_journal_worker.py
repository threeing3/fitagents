"""Disposable process for crash acceptance; isolated SQLite, not the live app."""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fast_api.app.services.durable_stream_journal import DurableStreamJournal  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("identity")
    parser.add_argument("owner")
    parser.add_argument("session")
    parser.add_argument("phase", choices=["before_commit", "after_commit"])
    args = parser.parse_args()
    directory = Path(args.directory)
    database = sqlite3.connect(directory / "business.sqlite")
    database.execute("CREATE TABLE observations (value TEXT NOT NULL)")
    database.commit()
    journal = DurableStreamJournal(args.identity, args.owner, args.session, str(directory))
    journal.append(
        {
            "type": "step",
            "event_id": "write-request",
            "stream_sequence": 1,
            "status": "running",
            "summary": "Synthetic write requested, not confirmed",
        }
    )
    database.execute("INSERT INTO observations VALUES (?)", ("synthetic",))
    if args.phase == "after_commit":
        database.commit()
        journal.append(
            {
                "type": "step",
                "event_id": "write-committed",
                "stream_sequence": 2,
                "status": "completed",
                "summary": "Synthetic transaction committed",
                "details": {"commit_observed": True},
            }
        )
    # Parent kills this exact process after acknowledgement; no Python cleanup runs.
    print("READY", flush=True)
    sys.stdin.read()


if __name__ == "__main__":
    main()
