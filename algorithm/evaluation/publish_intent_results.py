"""Publish a sanitized intent release summary from verified local evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_public_summary(
    release_manifest_path: Path,
    base_report_path: Path,
    adapter_report_path: Path,
    *,
    experiment_id: str,
) -> dict[str, Any]:
    release = _read(release_manifest_path)
    base = _read(base_report_path)
    adapter = _read(adapter_report_path)
    if release.get("release_status") != "approved" or not all(
        (release.get("gates") or {}).values()
    ):
        raise ValueError("adapter release is not approved")
    evidence = release.get("evidence") or {}
    if evidence.get("base_report_sha256") != _sha256(base_report_path):
        raise ValueError("base report checksum does not match the release manifest")
    if evidence.get("adapter_report_sha256") != _sha256(adapter_report_path):
        raise ValueError("adapter report checksum does not match the release manifest")
    base_metrics = base.get("with_deterministic_safety") or {}
    adapter_metrics = adapter.get("with_deterministic_safety") or {}
    required = ("exact_match", "schema_valid_rate", "risk_recall", "risk_cases")
    if any(name not in adapter_metrics for name in required):
        raise ValueError("adapter report is missing required public metrics")
    return {
        "schema_version": "fitagent-public-intent-release/v1",
        "experiment_id": experiment_id,
        "status": "verified_offline",
        "base_model": release.get("base_model"),
        "dataset": {
            "source": "fixed_challenge_test",
            "training_eligible": False,
            "cases": int(adapter_metrics.get("cases") or 0),
        },
        "metrics": {
            "base_exact_match": float(base_metrics.get("exact_match") or 0),
            "adapter_exact_match": float(adapter_metrics["exact_match"]),
            "adapter_schema_valid_rate": float(adapter_metrics["schema_valid_rate"]),
            "adapter_risk_recall": float(adapter_metrics["risk_recall"]),
            "risk_cases": int(adapter_metrics["risk_cases"]),
            "latency_ms": adapter.get("latency_ms") or {},
        },
        "adapter_sha256": release.get("adapter_sha256"),
        "evidence_sha256": {
            "release_manifest": _sha256(release_manifest_path),
            "base_report": _sha256(base_report_path),
            "adapter_report": _sha256(adapter_report_path),
        },
        "claims": {
            "offline_evaluation_verified": True,
            "online_business_uplift": False,
            "real_user_outcome": False,
        },
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish sanitized FitAgent intent evidence")
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--adapter-report", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build_public_summary(
        args.release_manifest,
        args.base_report,
        args.adapter_report,
        experiment_id=args.experiment_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"public summary already exists: {args.output}")
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
