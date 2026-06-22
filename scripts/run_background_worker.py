"""Run the database-backed background task worker.

Usage:
    python scripts/run_background_worker.py

The worker exits cleanly with Ctrl+C. It intentionally processes one task at a
time so local development is predictable; scale it by running more processes.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fast_api.app.db.database import SessionLocal
from fast_api.app.services.background_tasks import run_one_background_task


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Fitness background task worker")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="Process at most one task and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("background worker started")

    while True:
        with SessionLocal() as db:
            task = run_one_background_task(db)
            if task is not None:
                logging.info("processed task id=%s type=%s status=%s", task.id, task.task_type, task.status)
            elif args.once:
                logging.info("no queued task found")
                return

        if args.once:
            return
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
