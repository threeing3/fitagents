"""Optional DPO training entrypoint for validated preference pairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def train(config: dict[str, Any]) -> None:
    try:
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from trl import DPOTrainer
    except ImportError as exc:
        raise RuntimeError(
            "DPO dependencies are optional. Install algorithm/training/requirements-training.txt on AutoDL."
        ) from exc

    dataset = load_dataset("json", data_files=str(config["dataset_path"]), split="train")
    tokenizer = AutoTokenizer.from_pretrained(config["base_model"], trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(config["base_model"], trust_remote_code=True)
    lora = LoraConfig(
        r=int(config.get("lora_r", 16)),
        lora_alpha=int(config.get("lora_alpha", 32)),
        lora_dropout=float(config.get("lora_dropout", 0.05)),
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    args = TrainingArguments(
        output_dir=config["output_dir"],
        num_train_epochs=float(config.get("num_train_epochs", 1)),
        per_device_train_batch_size=int(config.get("per_device_train_batch_size", 1)),
        gradient_accumulation_steps=int(config.get("gradient_accumulation_steps", 8)),
        learning_rate=float(config.get("learning_rate", 5e-6)),
        logging_steps=5,
        report_to="none",
    )
    trainer = DPOTrainer(
        model=model,
        ref_model=None,
        args=args,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=lora,
    )
    trainer.train()
    trainer.save_model(config["output_dir"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DPO on validated preference pairs")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    train(json.loads(args.config.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
