"""Create immutable, readable records before an intent training run starts."""

from __future__ import annotations

import argparse
import json
import subprocess
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
    snapshot_id: str,
    row_limit: int | None = None,
    model_cache_root: str | None = None,
    offline_models: bool = False,
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
        "python3",
        "-m",
        "algorithm.training.sft.train_qlora",
        "--config",
        "algorithm/training/configs/intent_qwen3_4b_qlora.json",
        "--run-id",
        run_id,
    ]
    command_env: dict[str, str] = {}
    if model_cache_root:
        command_env["HF_HOME"] = model_cache_root
    if offline_models:
        command_env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
    command = {
        "argv": argv,
        "cwd": ".",
        "env": command_env,
        "created_at": _now(),
        "variant": variant,
        "row_limit": row_limit,
    }
    manifest = {
        "schema_version": "research-experiment/run-manifest-v1",
        "experiment_id": summary["experiment_id"],
        "run_id": run_id,
        "plan_revision": 1,
        "snapshot_id": snapshot_id,
        "variant": variant,
        "dataset": summary["dataset_version"],
        "split": "train+validation",
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
        "\n".join(
            [
                f"{_now()} prepared run {run_id} ({variant})",
                f"command={json.dumps(argv, ensure_ascii=False)}",
                f"train_dataset={summary['train_dataset_path']}",
                f"eval_dataset={summary['eval_dataset_path']}",
                f"dataset_version={summary['dataset_version']} seed={summary['seed']}",
                f"command_env_keys={sorted(command_env)}",
                "",
            ]
        ),
        encoding="utf-8",
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
    (records / "sync_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "fitagent-sync-manifest/v1",
                "run_id": run_id,
                "transfers": [],
                "note": "Append one entry for every snapshot or record transfer.",
            },
            ensure_ascii=False,
            indent=2,
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
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--row-limit", type=int)
    parser.add_argument(
        "--model-cache-root",
        help="Remote Hugging Face cache root recorded in the immutable command",
    )
    parser.add_argument(
        "--offline-models",
        action="store_true",
        help="Require model loading to use the pre-populated local cache",
    )
    args = parser.parse_args()
    run_dir = prepare_run(
        load_config(args.config),
        args.config,
        args.run_id,
        variant=args.variant,
        snapshot_id=args.snapshot_id,
        row_limit=args.row_limit,
        model_cache_root=args.model_cache_root,
        offline_models=args.offline_models,
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
