"""Tests for Alembic migration setup."""
import os

import pytest


# ---- Migration file exists ----

def test_alembic_ini_exists():
    ini_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic.ini"
    )
    assert os.path.exists(ini_path), "alembic.ini missing"


def test_alembic_env_exists():
    env_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "env.py"
    )
    assert os.path.exists(env_path), "alembic/env.py missing"


def test_initial_migration_exists():
    mig_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"
    )
    assert os.path.exists(mig_path), "001_initial_schema.py missing"


def test_migration_script_template_exists():
    tmpl_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "script.py.mako"
    )
    assert os.path.exists(tmpl_path), "script.py.mako missing"


# ---- Initial migration structure ----

def test_migration_has_revision_id():
    from importlib import util as import_util
    mig_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"
    )
    spec = import_util.spec_from_file_location("migration", mig_path)
    mod = import_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "001"
    assert mod.down_revision is None


def test_migration_has_upgrade_and_downgrade():
    from importlib import util as import_util
    mig_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"
    )
    spec = import_util.spec_from_file_location("migration", mig_path)
    mod = import_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_migration_creates_all_core_tables():
    """Verify the initial migration includes all expected table names."""
    with open(
        os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"),
        encoding="utf-8",
    ) as f:
        content = f.read()

    expected_tables = [
        "users",
        "user_profiles",
        "body_metrics",
        "fitness_goals",
        "conversation_sessions",
        "chat_messages",
        "training_plans",
        "workout_logs",
        "workout_sessions",
        "exercise_logs",
        "meal_logs",
        "nutrition_logs",
        "nutrition_daily_summaries",
        "food_items",
        "daily_checkins",
        "recovery_logs",
        "symptom_logs",
        "long_term_memories",
        "memory_blocks",
        "memory_catalog",
        "memory_exports",
        "agent_runs",
        "tool_calls",
        "agent_decisions",
        "risk_notes",
        "user_preferences",
        "prompt_versions",
        "eval_cases",
        "eval_runs",
        "eval_results",
        "explanation_knowledge",
        "fitness_decision_rules",
        "plan_templates",
        "coaching_cases",
    ]

    for table in expected_tables:
        assert f'"{table}"' in content, f"Missing table: {table}"


def test_migration_downgrade_drops_all_tables():
    with open(
        os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"),
        encoding="utf-8",
    ) as f:
        content = f.read()

    downgrade_start = content.find("def downgrade")
    downgrade_section = content[downgrade_start:]

    expected_drops = [
        "coaching_cases",
        "plan_templates",
        "fitness_decision_rules",
        "explanation_knowledge",
        "eval_results",
        "eval_runs",
        "eval_cases",
        "users",
    ]
    for table in expected_drops:
        assert f'"{table}"' in downgrade_section, f"Missing drop for: {table}"


# ---- database.py uses Alembic ----

def test_init_db_references_alembic():
    db_path = os.path.join(
        os.path.dirname(__file__), "..", "fast_api", "app", "db", "database.py"
    )
    with open(db_path, encoding="utf-8") as f:
        content = f.read()
    assert "alembic" in content, "init_db should reference alembic"
    assert "command.upgrade" in content, "init_db should call command.upgrade"


def test_database_py_no_longer_has_manual_migration():
    db_path = os.path.join(
        os.path.dirname(__file__), "..", "fast_api", "app", "db", "database.py"
    )
    with open(db_path, encoding="utf-8") as f:
        content = f.read()
    assert "_migrate_existing_schema" not in content, (
        "_migrate_existing_schema should be removed"
    )
    assert "ALTER TABLE" not in content, (
        "No raw ALTER TABLE statements should remain"
    )


# ---- Index coverage ----

def test_migration_includes_key_indexes():
    with open(
        os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "001_initial_schema.py"),
        encoding="utf-8",
    ) as f:
        content = f.read()

    expected_indexes = [
        "ix_long_term_memories_user_status",
        "ix_long_term_memories_type_status",
        "ix_long_term_memories_user_category",
        "ix_memory_catalog_user_category",
        "ix_memory_blocks_user_type",
        "ix_agent_decisions_user_type",
        "ix_risk_notes_user_status",
        "ix_symptom_logs_user_date",
        "ix_exercise_logs_user_exercise",
        "ix_explanation_knowledge_topic",
        "ix_fitness_decision_rules_intent_enabled",
        "ix_plan_templates_goal_level",
        "ix_coaching_cases_case_type",
    ]

    for idx in expected_indexes:
        assert idx in content, f"Missing index: {idx}"


def test_alembic_env_imports_base_metadata():
    env_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "env.py"
    )
    with open(env_path, encoding="utf-8") as f:
        content = f.read()
    assert "from fast_api.app.db.database import Base" in content
    assert "target_metadata = Base.metadata" in content


def test_alembic_env_imports_models():
    env_path = os.path.join(
        os.path.dirname(__file__), "..", "alembic", "env.py"
    )
    with open(env_path, encoding="utf-8") as f:
        content = f.read()
    assert "import fast_api.app.db.models" in content
