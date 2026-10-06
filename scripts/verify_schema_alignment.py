"""Validate the historical migration against synthetic legacy rows only."""

import argparse
import os
import uuid

from sqlalchemy import inspect, select, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Create a separate synthetic replay database; refuse existing",
    )
    args = parser.parse_args()
    database = "fitagent_schema_replay_20261002" if args.fresh else "fitagent_acceptance_20261002"
    if args.fresh:
        import psycopg
        from psycopg import sql

        with psycopg.connect(
            "host=127.0.0.1 port=15432 user=fitagent_test dbname=postgres", autocommit=True
        ) as connection:
            if connection.execute(
                "select 1 from pg_database where datname=%s", (database,)
            ).fetchone():
                raise ValueError("Synthetic replay database already exists; refusing to overwrite")
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    os.environ["DATABASE_URL"] = f"postgresql+psycopg://fitagent_test@127.0.0.1:15432/{database}"
    os.environ["USE_PGVECTOR"] = "false"
    from alembic import command
    from alembic.config import Config
    from fast_api.app.db import models
    from fast_api.app.db.database import SessionLocal, engine

    assert engine.url.database == database
    if args.fresh:
        command.upgrade(Config("alembic.ini"), "015_pending_approvals")
    marker = uuid.UUID("00000000-0000-4000-8000-000000001016")
    with engine.begin() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "015_pending_approvals":
            raise ValueError("Requires isolated pre-016 database; refusing to reseed or overwrite")
        statements = (
            "INSERT INTO users(id,email,password_hash,display_name) VALUES (:id,'schema-fixture@example.com','synthetic-not-a-login','Schema fixture')",
            "INSERT INTO user_preferences(user_id,language,coach_style,notification_enabled) VALUES (:id,'zh','gentle',false)",
            "INSERT INTO body_metrics(id,user_id,body_fat_pct) VALUES (:id,:id,18.5)",
            "INSERT INTO fitness_goals(id,user_id,goal_type,target_value) VALUES (:id,:id,'weight',68)",
            "INSERT INTO memory_blocks(id,user_id,block_type,block_key,summary) VALUES (:id,:id,'profile','fixture-profile','preserve this original summary')",
            "INSERT INTO fitness_decision_rules(id,rule_id,condition,action,enabled) VALUES (:id,'legacy-fixture','unstructured condition','unstructured action',true)",
            "INSERT INTO plan_templates(id,template_id,plan_schema,enabled) VALUES (:id,'legacy-template','{"
            + '"days": []'
            + "}',true)",
            "INSERT INTO explanation_knowledge(id,topic,content) VALUES (:id,'fixture','legacy knowledge')",
        )
        for statement in statements:
            connection.execute(text(statement), {"id": marker})
    command.upgrade(Config("alembic.ini"), "head")
    inspector = inspect(engine)
    for table in models.Base.metadata.sorted_tables:
        columns = inspector.get_columns(table.name)
        actual = {column["name"] for column in columns}
        assert set(table.columns.keys()) <= actual, table.name
        for column in columns:
            if column["name"] not in table.columns:
                continue
            expected = table.columns[column["name"]]
            assert not expected.nullable or column["nullable"], (table.name, expected.name)
            if str(expected.type) == "JSONB":
                assert str(column["type"]) == "JSONB", (table.name, expected.name)
    with SessionLocal() as db:
        # Real SQL for every mapped model, not metadata-created SQLite tables.
        for table in models.Base.metadata.sorted_tables:
            db.execute(select(table).limit(1)).all()
        assert db.get(models.BodyMetric, marker).body_fat_percent == 18.5
        assert db.get(models.FitnessGoal, marker).target == "68"
        assert db.get(models.MemoryBlock, marker).content == "preserve this original summary"
        assert db.get(models.FitnessDecisionRule, marker).enabled is False
        assert db.get(models.PlanTemplate, marker).enabled is False
        assert db.get(models.ExplanationKnowledge, marker).status == "legacy_unreviewed"
        original = db.execute(
            text(
                "SELECT language,coach_style,notification_enabled FROM user_preferences WHERE user_id=:id"
            ),
            {"id": marker},
        ).one()
        assert original == ("zh", "gentle", False)
        for category in ("exercise", "nutrition"):
            db.add(
                models.UserPreference(
                    user_id=marker, category=category, content="synthetic preference"
                )
            )
        db.commit()
        assert db.query(models.UserPreference).filter_by(user_id=marker).count() == 3
    # Re-entry must not disable new rules or overwrite translated content.
    import importlib.util
    from unittest.mock import patch

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    specification = importlib.util.spec_from_file_location(
        "alignment", "alembic/versions/016_domain_schema_alignment.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    with engine.begin() as connection:
        with patch.object(module, "op", Operations(MigrationContext.configure(connection))):
            module.upgrade()
    print(
        "PASS: all mapped columns selectable, legacy values retained, unsafe legacy rules disabled, multiple preferences supported, re-entry succeeds",
        flush=True,
    )


if __name__ == "__main__":
    main()
