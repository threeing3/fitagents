import asyncio
import json

from fast_api.app.services.jev_intent_client import JevIntentResult
from scripts import check_jev_intent as probe


def read_log(directory):
    paths = list(directory.glob("jev_probe_*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


def test_offline_probe_saves_log_without_network(tmp_path, monkeypatch):
    async def forbidden(*args):
        raise AssertionError("No network in offline mode")

    monkeypatch.setattr(probe.JevIntentClient, "classify", forbidden)
    asyncio.run(probe.run(False, log_dir=tmp_path))
    entries = read_log(tmp_path)
    assert len(entries) == 7
    assert entries[-1]["estimated_known_cost_usd"] == 0


def test_budget_prevents_first_call(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.getpass, "getpass", lambda _: "test-key")

    async def forbidden(*args):
        raise AssertionError("Budget must reject before network")

    monkeypatch.setattr(probe.JevIntentClient, "classify", forbidden)
    asyncio.run(probe.run(True, budget_usd=0.001, log_dir=tmp_path))
    assert read_log(tmp_path)[1]["reason"] == "budget_reservation_limit"


def test_first_failure_stops_and_keeps_unknown_cost_reserved(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.getpass, "getpass", lambda _: "test-key")
    calls = []

    async def fail(self, message):
        calls.append(message)
        return JevIntentResult(True, False, "timeout")

    monkeypatch.setattr(probe.JevIntentClient, "classify", fail)
    asyncio.run(probe.run(True, log_dir=tmp_path))
    entries = read_log(tmp_path)
    assert len(calls) == 1
    assert entries[1]["estimated_cost_usd"] is None
    assert entries[-1]["reserved_upper_bound_usd"] > 0
    assert "test-key" not in json.dumps(entries)
