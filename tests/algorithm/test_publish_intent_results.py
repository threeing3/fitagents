import hashlib
import json
from pathlib import Path

import pytest

from algorithm.evaluation.publish_intent_results import build_public_summary
from fast_api.app.api import algorithm_api


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_public_summary_contains_metrics_and_no_raw_predictions(tmp_path):
    base = _write(
        tmp_path / "base.json",
        {"with_deterministic_safety": {"exact_match": 0.6}},
    )
    adapter = _write(
        tmp_path / "adapter.json",
        {
            "with_deterministic_safety": {
                "cases": 120,
                "exact_match": 0.8,
                "schema_valid_rate": 1.0,
                "risk_recall": 1.0,
                "risk_cases": 30,
            },
            "latency_ms": {"p50": 42, "p95": 58},
            "predictions": [{"user_message": "private"}],
        },
    )
    release = _write(
        tmp_path / "release.json",
        {
            "release_status": "approved",
            "gates": {"schema": True, "safety": True},
            "base_model": "Qwen/Qwen3-4B",
            "adapter_sha256": "abc123",
            "evidence": {
                "base_report_sha256": _sha(base),
                "adapter_report_sha256": _sha(adapter),
            },
        },
    )
    summary = build_public_summary(release, base, adapter, experiment_id="intent-test")
    assert summary["status"] == "verified_offline"
    assert summary["metrics"]["adapter_risk_recall"] == 1.0
    assert summary["claims"]["online_business_uplift"] is False
    assert "private" not in json.dumps(summary)
    assert "predictions" not in summary


def test_public_summary_rejects_report_not_bound_to_release(tmp_path):
    base = _write(tmp_path / "base.json", {"with_deterministic_safety": {"exact_match": 0.6}})
    adapter = _write(tmp_path / "adapter.json", {"with_deterministic_safety": {}})
    release = _write(
        tmp_path / "release.json",
        {
            "release_status": "approved",
            "gates": {"all": True},
            "evidence": {
                "base_report_sha256": "wrong",
                "adapter_report_sha256": _sha(adapter),
            },
        },
    )
    with pytest.raises(ValueError, match="base report checksum"):
        build_public_summary(release, base, adapter, experiment_id="intent-test")


def test_api_release_reader_rejects_unverified_or_wrong_schema(tmp_path, monkeypatch):
    report = tmp_path / "public.json"
    monkeypatch.setattr(algorithm_api, "INTENT_RELEASE_REPORT_PATH", report)
    _write(report, {"schema_version": "wrong", "status": "verified_offline"})
    assert algorithm_api._intent_release() is None
    _write(
        report,
        {
            "schema_version": "fitagent-public-intent-release/v1",
            "status": "verified_offline",
            "metrics": {"adapter_exact_match": 0.8},
            "predictions": [{"user_message": "must-not-leak"}],
        },
    )
    loaded = algorithm_api._intent_release()
    assert loaded["metrics"]["adapter_exact_match"] == 0.8
    assert "must-not-leak" not in json.dumps(loaded)
