import json

import pytest

from algorithm.training.dpo.train_dpo import validate_config as validate_dpo_config
from algorithm.training.sft.train_qlora import build_prompt, train as train_sft, validate_config as validate_sft_config


def test_sft_dry_run_validates_dataset_without_gpu_imports(tmp_path):
    dataset = tmp_path / "sft.jsonl"
    dataset.write_text(json.dumps({"messages": [{"role": "user", "content": "plan"}]}) + "\n", encoding="utf-8")
    config = {"base_model": "demo/model", "dataset_path": str(dataset), "output_dir": str(tmp_path / "adapter")}
    summary = validate_sft_config(config)
    assert summary["rows"] == 1
    assert train_sft(config, dry_run=True)["dataset_path"] == str(dataset.resolve())
    assert "user:" in build_prompt({"messages": [{"role": "user", "content": "hello"}]})


def test_dpo_dry_run_rejects_invalid_pairs(tmp_path):
    dataset = tmp_path / "pairs.jsonl"
    dataset.write_text(json.dumps({"prompt": "q", "chosen": "same", "rejected": "same"}) + "\n", encoding="utf-8")
    config = {"base_model": "demo/model", "dataset_path": str(dataset), "output_dir": str(tmp_path / "adapter")}
    with pytest.raises(ValueError, match="identical"):
        validate_dpo_config(config)
