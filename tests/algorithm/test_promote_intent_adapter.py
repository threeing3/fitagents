import json
from pathlib import Path

import pytest

from algorithm.training.promote_intent_adapter import promote, validate_release_manifest


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _evidence(tmp_path: Path, *, adapter_exact: float = 0.8, risk_recall: float = 1.0):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"verified-adapter")
    reload_report = _write(
        tmp_path / "reload.json",
        {
            "verified": True,
            "base_model": "Qwen/Qwen3-4B",
            "adapter_path": str(adapter.resolve()),
        },
    )
    base_report = _write(
        tmp_path / "base.json",
        {
            "source": "fixed_challenge_test",
            "adapter": None,
            "with_deterministic_safety": {"exact_match": 0.7},
        },
    )
    adapter_report = _write(
        tmp_path / "adapted.json",
        {
            "source": "fixed_challenge_test",
            "adapter": str(adapter),
            "with_deterministic_safety": {
                "exact_match": adapter_exact,
                "schema_valid_rate": 1.0,
                "risk_recall": risk_recall,
            },
        },
    )
    return adapter, reload_report, base_report, adapter_report


def test_promotion_binds_verified_reports_to_adapter_checksum(tmp_path):
    adapter, reload_report, base_report, adapter_report = _evidence(tmp_path)

    manifest = promote(adapter, reload_report, base_report, adapter_report)

    assert manifest["release_status"] == "approved"
    assert validate_release_manifest(adapter)["gates"]["risk_recall"] is True
    (adapter / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        validate_release_manifest(adapter)


def test_promotion_rejects_adapter_that_does_not_improve_or_recall_risk(tmp_path):
    adapter, reload_report, base_report, adapter_report = _evidence(
        tmp_path, adapter_exact=0.7, risk_recall=0.97
    )

    with pytest.raises(ValueError, match="risk_recall, exact_match_improved"):
        promote(adapter, reload_report, base_report, adapter_report)
