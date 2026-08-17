import json

import pytest

from algorithm.training.dpo.train_dpo import validate_config as validate_dpo_config
from algorithm.training.sft.train_qlora import tokenize_with_assistant_mask
from algorithm.training.sft.train_qlora import (
    train as train_sft,
)
from algorithm.training.sft.train_qlora import (
    validate_config as validate_sft_config,
)


def test_sft_dry_run_validates_dataset_without_gpu_imports(tmp_path):
    train_dataset = tmp_path / "train.jsonl"
    eval_dataset = tmp_path / "eval.jsonl"
    train_row = {
        "example_id": "train-1",
        "template_family": "train-family",
        "messages": [
            {"role": "user", "content": "plan"},
            {"role": "assistant", "content": "{}"},
        ],
    }
    eval_row = {
        "example_id": "eval-1",
        "template_family": "eval-family",
        "messages": [
            {"role": "user", "content": "risk"},
            {"role": "assistant", "content": "{}"},
        ],
    }
    train_dataset.write_text(json.dumps(train_row) + "\n", encoding="utf-8")
    eval_dataset.write_text(json.dumps(eval_row) + "\n", encoding="utf-8")
    config = {
        "experiment_id": "intent-test",
        "base_model": "Qwen/Qwen3-4B",
        "train_dataset_path": str(train_dataset),
        "eval_dataset_path": str(eval_dataset),
        "output_root": str(tmp_path / "runs"),
        "dataset_version": "test-v1",
        "assistant_only_loss": True,
        "enable_thinking": False,
    }
    summary = validate_sft_config(config)
    assert summary["train_rows"] == 1
    assert summary["eval_rows"] == 1
    assert train_sft(config, dry_run=True)["train_dataset_path"] == str(train_dataset.resolve())


def test_sft_tokenization_masks_non_assistant_tokens():
    class FakeTokenizer:
        chat_template = "{% generation %}assistant{% endgeneration %}"

        def apply_chat_template(self, messages, **kwargs):
            assert kwargs["return_assistant_tokens_mask"] is True
            assert kwargs["enable_thinking"] is False
            return {
                "input_ids": [10, 11, 12, 13],
                "attention_mask": [1, 1, 1, 1],
                "assistant_masks": [0, 0, 1, 1],
            }

    encoded = tokenize_with_assistant_mask(
        FakeTokenizer(),
        {"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]},
        128,
    )
    assert encoded["labels"] == [-100, -100, 12, 13]


def test_sft_tokenization_falls_back_to_final_assistant_boundary():
    class QwenStyleTokenizer:
        chat_template = "no generation block"

        def apply_chat_template(self, messages, **kwargs):
            if kwargs["add_generation_prompt"]:
                return {"input_ids": [1, 2, 3], "attention_mask": [1, 1, 1]}
            return {"input_ids": [1, 2, 3, 4, 5], "attention_mask": [1, 1, 1, 1, 1]}

    encoded = tokenize_with_assistant_mask(
        QwenStyleTokenizer(),
        {"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]},
        128,
    )
    assert encoded["labels"] == [-100, -100, -100, 4, 5]


def test_sft_validation_rejects_template_family_leakage(tmp_path):
    row = {
        "example_id": "train",
        "template_family": "leaked-family",
        "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "{}"},
        ],
    }
    train_dataset = tmp_path / "train.jsonl"
    eval_dataset = tmp_path / "eval.jsonl"
    train_dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    row["example_id"] = "eval"
    eval_dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    config = {
        "experiment_id": "intent-test",
        "base_model": "Qwen/Qwen3-4B",
        "train_dataset_path": str(train_dataset),
        "eval_dataset_path": str(eval_dataset),
        "output_root": str(tmp_path / "runs"),
        "dataset_version": "test-v1",
        "assistant_only_loss": True,
        "enable_thinking": False,
    }
    with pytest.raises(ValueError, match="template_family"):
        validate_sft_config(config)


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
