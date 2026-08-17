"""Verify immutable training records before any technical success claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

REQUIRED_RECORDS = {
    "command.json",
    "environment.json",
    "events.jsonl",
    "metrics.jsonl",
    "nvidia_smi.txt",
    "output_manifest.json",
    "pip_freeze.txt",
    "resource_usage.jsonl",
    "run.log",
    "run_manifest.json",
    "run_summary.json",
    "status.json",
    "sync_manifest.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def verify_run(run_dir: Path, *, require_adapter_reload: bool = False) -> dict[str, Any]:
    records = run_dir / "records"
    errors: list[str] = []
    missing = sorted(name for name in REQUIRED_RECORDS if not (records / name).is_file())
    errors.extend(f"missing record: {name}" for name in missing)
    if errors:
        report = {"verified": False, "errors": errors, "run_dir": str(run_dir)}
        if records.is_dir():
            (records / "verification_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return report
    status = _read_json(records / "status.json")
    if status.get("status") != "completed-technical":
        errors.append(f"unexpected status: {status.get('status')}")
    manifest = _read_json(records / "run_manifest.json")
    summary = _read_json(records / "run_summary.json")
    if manifest.get("run_id") != summary.get("run_id"):
        errors.append("run_id differs between manifest and summary")
    for key in ("dataset_version", "train_sha256", "eval_sha256", "seed"):
        if manifest.get(key) != summary.get(key):
            errors.append(f"{key} differs between manifest and summary")
    events = _read_jsonl(records / "events.jsonl")
    if not events or events[-1].get("event") != "completed-technical":
        errors.append("events do not end with completed-technical")
    metrics = _read_jsonl(records / "metrics.jsonl")
    if not metrics:
        errors.append("metrics.jsonl is empty")
    for row in metrics:
        for key, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"non-finite metric {key}")
    output_manifest = _read_json(records / "output_manifest.json")
    adapter = run_dir / "adapter"
    files = output_manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("output manifest has no adapter files")
    else:
        for item in files:
            path = adapter / str(item.get("path"))
            if not path.is_file():
                errors.append(f"adapter output missing: {item.get('path')}")
                continue
            if path.stat().st_size != item.get("bytes") or _sha256(path) != item.get("sha256"):
                errors.append(f"adapter output checksum mismatch: {item.get('path')}")
    reload_report_path = records / "adapter_reload_report.json"
    if require_adapter_reload:
        if not reload_report_path.is_file():
            errors.append("adapter reload report is required")
        elif _read_json(reload_report_path).get("verified") is not True:
            errors.append("adapter reload report did not pass")
    report = {
        "schema_version": "fitagent-run-verification/v1",
        "run_id": manifest.get("run_id"),
        "verified": not errors,
        "require_adapter_reload": require_adapter_reload,
        "metric_observations": len(metrics),
        "adapter_file_count": len(files) if isinstance(files, list) else 0,
        "errors": errors,
    }
    (records / "verification_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one immutable intent training run")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--require-adapter-reload", action="store_true")
    args = parser.parse_args()
    report = verify_run(args.run_dir, require_adapter_reload=args.require_adapter_reload)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
