"""Create immutable, readable records before an intent training run starts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from algorithm.training.sft.train_qlora import _resolve_path, load_config, validate_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_run(
    config: dict[str, Any],
    config_path: Path,
    run_id: str,
    *,
    variant: str,
    row_limit: int | None = None,
) -> Path:
    """Create a new run record; an existing run ID is never overwritten."""

    summary = validate_config(config, config_path)
    output_root = _resolve_path(str(config["output_root"]), config_path)
    run_dir = output_root / run_id
    if run_dir.exists():
        raise FileExistsError(f"run already exists; choose a new immutable run_id: {run_dir}")
    records = run_dir / "records"
    records.mkdir(parents=True)
    argv = [
        sys.executable,
        "-m",
        "algorithm.training.sft.train_qlora",
        "--config",
        str(config_path.resolve()),
        "--run-id",
        run_id,
    ]
    command = {
        "argv": argv,
        "cwd": str(Path.cwd().resolve()),
        "created_at": _now(),
        "variant": variant,
        "row_limit": row_limit,
    }
    manifest = {
        "schema_version": "fitagent-run-manifest/v1",
        "experiment_id": summary["experiment_id"],
        "run_id": run_id,
        "variant": variant,
        "dataset_version": summary["dataset_version"],
        "train_sha256": summary["train_sha256"],
        "eval_sha256": summary["eval_sha256"],
        "seed": summary["seed"],
        "row_limit": row_limit,
        "status": "prepared",
    }
    (records / "command.json").write_text(
        json.dumps(command, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (records / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (records / "status.json").write_text(
        json.dumps({"status": "prepared", "updated_at": _now()}, indent=2) + "\n",
        encoding="utf-8",
    )
    (records / "run.log").write_text(
        f"{_now()} prepared run {run_id} ({variant})\n", encoding="utf-8"
    )
    (records / "events.jsonl").write_text(
        json.dumps({"timestamp": _now(), "event": "run_prepared"}) + "\n", encoding="utf-8"
    )
    (records / "metrics.jsonl").write_text("", encoding="utf-8")
    try:
        nvidia = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, check=False, timeout=20
        )
        nvidia_text = nvidia.stdout or nvidia.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        nvidia_text = f"nvidia-smi unavailable: {exc}\n"
    (records / "nvidia_smi.txt").write_text(nvidia_text, encoding="utf-8")
    (records / "resource_usage.jsonl").write_text(
        json.dumps(
            {
                "timestamp": _now(),
                "event": "preflight",
                "nvidia_smi_available": "NVIDIA-SMI" in nvidia_text,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare immutable intent-training records")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--variant", choices=["smoke", "full"], required=True)
    parser.add_argument("--row-limit", type=int)
    args = parser.parse_args()
    run_dir = prepare_run(
        load_config(args.config),
        args.config,
        args.run_id,
        variant=args.variant,
        row_limit=args.row_limit,
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
