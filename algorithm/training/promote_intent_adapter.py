"""Promote a verified intent adapter by writing a content-bound release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algorithm.training.verify_adapter_reload import validate_adapter_directory

RELEASE_MANIFEST = "fitagent_release_manifest.json"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must contain a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def promote(
    adapter_path: Path,
    reload_report_path: Path,
    base_report_path: Path,
    adapter_report_path: Path,
) -> dict[str, Any]:
    adapter_path = adapter_path.resolve()
    validate_adapter_directory(adapter_path)
    reload_report = _read(reload_report_path)
    base_report = _read(base_report_path)
    adapter_report = _read(adapter_report_path)
    if reload_report.get("verified") is not True:
        raise ValueError("fresh-process adapter reload is not verified")
    if Path(str(reload_report.get("adapter_path") or "")).resolve() != adapter_path:
        raise ValueError("reload report belongs to a different adapter")
    if base_report.get("source") != "fixed_challenge_test" or base_report.get("adapter"):
        raise ValueError("base report is not the frozen unadapted baseline")
    if adapter_report.get("source") != "fixed_challenge_test" or not adapter_report.get("adapter"):
        raise ValueError("adapter report is not a frozen adapter evaluation")
    base_metrics = base_report.get("with_deterministic_safety") or {}
    adapter_metrics = adapter_report.get("with_deterministic_safety") or {}
    gates = {
        "schema_valid_rate": float(adapter_metrics.get("schema_valid_rate") or 0) >= 0.99,
        "risk_recall": float(adapter_metrics.get("risk_recall") or 0) >= 0.98,
        "exact_match_improved": float(adapter_metrics.get("exact_match") or 0)
        > float(base_metrics.get("exact_match") or 0),
        "reload_verified": True,
    }
    if not all(gates.values()):
        failed = ", ".join(name for name, passed in gates.items() if not passed)
        raise ValueError(f"adapter release gates failed: {failed}")
    weights = adapter_path / "adapter_model.safetensors"
    manifest = {
        "schema_version": "fitagent-intent-adapter-release/v1",
        "release_status": "approved",
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "base_model": reload_report.get("base_model"),
        "adapter_sha256": _sha256(weights),
        "gates": gates,
        "metrics": {
            "base_exact_match": base_metrics.get("exact_match"),
            "adapter_exact_match": adapter_metrics.get("exact_match"),
            "adapter_schema_valid_rate": adapter_metrics.get("schema_valid_rate"),
            "adapter_risk_recall": adapter_metrics.get("risk_recall"),
        },
        "evidence": {
            "reload_report_sha256": _sha256(reload_report_path),
            "base_report_sha256": _sha256(base_report_path),
            "adapter_report_sha256": _sha256(adapter_report_path),
        },
    }
    output = adapter_path / RELEASE_MANIFEST
    if output.exists():
        raise FileExistsError(f"release manifest already exists: {output}")
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_release_manifest(adapter_path: Path) -> dict[str, Any]:
    adapter_path = adapter_path.resolve()
    manifest_path = adapter_path / RELEASE_MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"adapter is not promoted; missing: {RELEASE_MANIFEST}")
    manifest = _read(manifest_path)
    if manifest.get("schema_version") != "fitagent-intent-adapter-release/v1":
        raise ValueError("adapter release manifest schema is invalid")
    if manifest.get("release_status") != "approved" or not all(
        (manifest.get("gates") or {}).values()
    ):
        raise ValueError("adapter release manifest is not approved")
    actual = _sha256(adapter_path / "adapter_model.safetensors")
    if manifest.get("adapter_sha256") != actual:
        raise ValueError("adapter checksum does not match its release manifest")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote a verified FitAgent intent adapter")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--reload-report", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--adapter-report", type=Path, required=True)
    args = parser.parse_args()
    manifest = promote(args.adapter, args.reload_report, args.base_report, args.adapter_report)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
