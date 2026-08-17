import json

import pytest

from algorithm.training.dpo.train_dpo import validate_config as validate_dpo_config
from algorithm.training.sft.train_qlora import (
    build_prompt,
)
from algorithm.training.sft.train_qlora import (
    train as train_sft,
)
from algorithm.training.sft.train_qlora import (
    validate_config as validate_sft_config,
)


def test_sft_dry_run_validates_dataset_without_gpu_imports(tmp_path):
    dataset = tmp_path / "sft.jsonl"
    dataset.write_text(
        json.dumps({"messages": [{"role": "user", "content": "plan"}]}) + "\n", encoding="utf-8"
    )
    config = {
        "base_model": "demo/model",
        "dataset_path": str(dataset),
        "output_dir": str(tmp_path / "adapter"),
    }
    summary = validate_sft_config(config)
    assert summary["rows"] == 1
    assert train_sft(config, dry_run=True)["dataset_path"] == str(dataset.resolve())
    assert "user:" in build_prompt({"messages": [{"role": "user", "content": "hello"}]})


def test_dpo_dry_run_rejects_invalid_pairs(tmp_path):
    dataset = tmp_path / "pairs.jsonl"
    dataset.write_text(
        json.dumps({"prompt": "q", "chosen": "same", "rejected": "same"}) + "\n", encoding="utf-8"
    )
    config = {
        "base_model": "demo/model",
        "dataset_path": str(dataset),
        "output_dir": str(tmp_path / "adapter"),
    }
    with pytest.raises(ValueError, match="identical"):
        validate_dpo_config(config)


def test_dpo_stays_blocked_below_human_review_threshold(tmp_path):
    dataset = tmp_path / "pairs.jsonl"
    rows = [
        {
            "prompt": f"q-{index}",
            "chosen": "safe specific answer",
            "rejected": "unsafe vague answer",
            "source": "expert_labeled",
            "review_status": "approved",
        }
        for index in range(149)
    ]
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    config = {
        "base_model": "demo/model",
        "dataset_path": str(dataset),
        "output_dir": str(tmp_path / "adapter"),
    }
    with pytest.raises(ValueError, match="at least 150 human-reviewed pairs"):
        validate_dpo_config(config)
