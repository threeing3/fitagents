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
        plan_revision=2,
        row_limit=50,
        model_cache_root="/root/autodl-tmp/weights/huggingface",
        offline_models=True,
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
    assert command["argv"][4] == config_path.as_posix()
    assert command["cwd"] == "."
    assert command["env"] == {
        "HF_HOME": "/root/autodl-tmp/weights/huggingface",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    manifest = json.loads((records / "run_manifest.json").read_text())
    assert manifest["snapshot_id"] == "snapshot-123"
    assert manifest["plan_revision"] == 2
    assert manifest["claim_scope"] == "diagnostic_only"
    with pytest.raises(FileExistsError, match="immutable run_id"):
        prepare_run(
            config,
            config_path,
            "smoke-001",
            variant="smoke",
            snapshot_id="snapshot-123",
            row_limit=50,
        )


def test_full_run_requires_matching_admitted_rows_before_creating_run(tmp_path):
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
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="requires an admission report"):
        prepare_run(config, config_path, "full-001", variant="full", snapshot_id="snapshot-1")
    assert not (tmp_path / "runs" / "full-001").exists()

    admission_path = tmp_path / "admission.json"
    admission = {
        "schema_version": "fitagent-intent-training-admission/v2",
        "quality_training_admitted": False,
        "structural_checks_pass": True,
        "human_review_complete_for_train": True,
        "human_review_complete_for_validation": True,
        "scenario_split_verified": True,
        "eligible_example_ids_by_split": {"train": ["train"], "validation": ["validation"]},
    }
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    with pytest.raises(ValueError, match="did not pass quality admission"):
        prepare_run(
            config,
            config_path,
            "full-001",
            variant="full",
            snapshot_id="snapshot-1",
            admission_report_path=admission_path,
        )

    admission["quality_training_admitted"] = True
    admission["eligible_example_ids_by_split"]["validation"] = ["wrong-id"]
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    with pytest.raises(ValueError, match="validation SFT rows do not match"):
        prepare_run(
            config,
            config_path,
            "full-001",
            variant="full",
            snapshot_id="snapshot-1",
            admission_report_path=admission_path,
        )

    admission["eligible_example_ids_by_split"]["validation"] = ["validation"]
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    run_dir = prepare_run(
        config,
        config_path,
        "full-001",
        variant="full",
        snapshot_id="snapshot-1",
        admission_report_path=admission_path,
    )
    manifest = json.loads((run_dir / "records" / "run_manifest.json").read_text())
    assert manifest["claim_scope"] == "quality_candidate"
    assert manifest["admission_report"] == str(admission_path)
