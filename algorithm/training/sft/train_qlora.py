"""Train a small instruction adapter with QLoRA on AutoDL.

Imports are intentionally lazy so the application environment can run data
validation and offline evaluation without installing GPU libraries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(example: dict[str, Any]) -> str:
    messages = example.get("messages") or []
    return "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in messages)


def train(config: dict[str, Any]) -> None:
    try:
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Training dependencies are optional. Install algorithm/training/requirements-training.txt on AutoDL."
        ) from exc

    dataset = load_dataset("json", data_files=str(config["dataset_path"]), split="train")
    tokenizer = AutoTokenizer.from_pretrained(config["base_model"], trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize(row: dict[str, Any]) -> dict[str, Any]:
        encoded = tokenizer(
            build_prompt(row),
            max_length=int(config.get("max_seq_length", 2048)),
            truncation=True,
            padding="max_length",
        )
        encoded["labels"] = list(encoded["input_ids"])
        return encoded

    tokenized = dataset.map(tokenize, remove_columns=dataset.column_names)
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="bfloat16",
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config["base_model"],
        quantization_config=quant,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(
        r=int(config.get("lora_r", 16)),
        lora_alpha=int(config.get("lora_alpha", 32)),
        lora_dropout=float(config.get("lora_dropout", 0.05)),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    args = TrainingArguments(
        output_dir=config["output_dir"],
        num_train_epochs=float(config.get("num_train_epochs", 2)),
        per_device_train_batch_size=int(config.get("per_device_train_batch_size", 1)),
        gradient_accumulation_steps=int(config.get("gradient_accumulation_steps", 8)),
        learning_rate=float(config.get("learning_rate", 2e-4)),
        warmup_ratio=float(config.get("warmup_ratio", 0.05)),
        logging_steps=int(config.get("logging_steps", 5)),
        save_steps=int(config.get("save_steps", 100)),
        seed=int(config.get("seed", 42)),
        bf16=True,
        report_to="none",
        remove_unused_columns=False,
    )
    trainer = Trainer(model=model, args=args, train_dataset=tokenized, tokenizer=tokenizer)
    trainer.train()
    trainer.save_model(config["output_dir"])
    tokenizer.save_pretrained(config["output_dir"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QLoRA SFT")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    train(load_config(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
