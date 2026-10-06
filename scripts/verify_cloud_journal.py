"""Validate only the explicitly selected empty Neon demo database, never local DBs."""

import argparse
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from fast_api.app.core.config import get_settings
from fast_api.app.db.database import engine
from fast_api.app.services.durable_stream_journal import DurableStreamJournal, read_stream_journal

DEMO_HOST = "ep-blue-hall-b5dld7rs-pooler.c-7.us-east-2.aws.neon.tech"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["initialize", "read"])
    parser.add_argument("identity")
    args = parser.parse_args()
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.host != DEMO_HOST or url.database != "neondb":
        raise SystemExit("Refusing unexpected database target")
    if settings.stream_journal_backend != "database" or settings.use_pgvector:
        raise SystemExit("Refusing unexpected diagnostic configuration")
    identity = uuid.UUID(args.identity)
    if args.stage == "initialize":
        if inspect(engine).get_table_names():
            raise SystemExit("Refusing non-empty database initialization")
        command.upgrade(Config("alembic.ini"), "head")
        journal = DurableStreamJournal(identity, "cloud-verification", None)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(
                pool.map(
                    lambda n: journal.append(
                        {
                            "type": "step",
                            "event_id": f"synthetic-{n}",
                            "api_key": "synthetic-redaction-check",
                        }
                    ),
                    range(12),
                )
            )
        journal.close()
        print("Empty demo database migrated; 12 concurrent synthetic events committed.")
    else:
        saved = read_stream_journal(identity, "cloud-verification", None, limit=5)
        assert saved["recorded_count"] == 12 and saved["next_position"] == 5
        assert saved["state"] == "unconfirmed" and saved["has_more"]
        assert "synthetic-redaction-check" not in str(saved)
        assert read_stream_journal(identity, "different-owner", None) is None
        rest = read_stream_journal(identity, "cloud-verification", None, after=5)
        assert len(rest["entries"]) == 7
        assert len({item["event_id"] for item in saved["entries"] + rest["entries"]}) == 12
        print(
            "Fresh process verified persisted events, stable pagination, redaction and isolation."
        )
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "018_stream_journals"
        )
    engine.dispose()


if __name__ == "__main__":
    main()
