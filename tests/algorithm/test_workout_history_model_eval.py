import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine

from algorithm.evaluation.workout_history_model_eval import CASES, evaluate_case, run, seed_database


def test_scripted_actual_reader_cases_and_replay_database(tmp_path):
    report = asyncio.run(run(tmp_path / "run"))
    assert report["passed"] == 4
    assert report["mode"] == "scripted_model_actual_reader_sqlite"
    assert (tmp_path / "run" / "synthetic.sqlite").is_file()
    assert (tmp_path / "run" / "fixture.json").is_file()


def test_correct_verbal_answer_without_database_tool_does_not_pass():
    class SkippingModel:
        async def ainvoke(self, messages):
            return SimpleNamespace(content='{"status":"found","duration_minutes":18}')

    engine = create_engine("sqlite:///:memory:")
    try:
        seed_database(engine)
        row = asyncio.run(evaluate_case(engine, CASES[0], SkippingModel(), mode="scripted"))
        assert row["checks"]["answer_correct"]
        assert not row["checks"]["tool_executed"]
        assert not row["passed"]
    finally:
        engine.dispose()
