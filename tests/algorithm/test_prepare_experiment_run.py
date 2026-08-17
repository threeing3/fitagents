import json

import pytest

from algorithm.training.prepare_experiment_run import prepare_run


def _dataset(path, example_id, family):
    path.write_text(
        json.dumps(
            {
                "example_id": example_id,
                "template_family": family,
                "messages": [
                    {"role": "user", "content": "x"},
                    {"role": "assistant", "content": "{}"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_prepare_run_creates_required_records_and_refuses_overwrite(tmp_path):
    train = tmp_path / "train.jsonl"
    validation = tmp_path / "validation.jsonl"
    _dataset(train, "train", "train-family")
    _dataset(validation, "validation", "validation-family")
    config_path = tmp_path / "config.json"
    config = {
        "experiment_id": "intent-test",
        "base_model": "Qwen/Qwen3-4B",
        "train_dataset_path": str(train),
        "eval_dataset_path": str(validation),
        "output_root": str(tmp_path / "runs"),
        "dataset_version": "test-v1",
        "assistant_only_loss": True,
        "enable_thinking": False,
        "seed": 42,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    run_dir = prepare_run(
        config,
        config_path,
        "smoke-001",
        variant="smoke",
        snapshot_id="snapshot-123",
        row_limit=50,
    )
    records = run_dir / "records"
    assert {
        "command.json",
        "events.jsonl",
        "metrics.jsonl",
        "nvidia_smi.txt",
        "resource_usage.jsonl",
        "run.log",
        "run_manifest.json",
        "status.json",
        "sync_manifest.json",
    }.issubset({path.name for path in records.iterdir()})
    assert json.loads((records / "run_manifest.json").read_text())["row_limit"] == 50
    command = json.loads((records / "command.json").read_text())
    assert command["argv"][0] == "python3"
    assert command["cwd"] == "."
    assert json.loads((records / "run_manifest.json").read_text())["snapshot_id"] == "snapshot-123"
    with pytest.raises(FileExistsError, match="immutable run_id"):
        prepare_run(
            config,
            config_path,
            "smoke-001",
            variant="smoke",
            snapshot_id="snapshot-123",
            row_limit=50,
        )
