"""Train a small instruction adapter with QLoRA on AutoDL.

Imports are intentionally lazy so the application environment can run data
validation and offline evaluation without installing GPU libraries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def resolve_dataset_path(config: dict[str, Any], config_path: Path | None = None) -> Path:
    raw = Path(str(config["dataset_path"]))
    if raw.is_absolute():
        return raw
    candidates = [Path.cwd() / raw]
    if config_path is not None:
        candidates.append(config_path.parent / raw)
        if len(config_path.parents) >= 4:
            candidates.append(config_path.parents[3] / raw)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_prompt(example: dict[str, Any]) -> str:
    messages = example.get("messages") or []
    return "\n".join(f"{item.get('role', 'user')}: {item.get('content', '')}" for item in messages)


def validate_config(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    required = ["base_model", "dataset_path", "output_dir"]
    missing = [key for key in required if not config.get(key)]
    dataset_path = resolve_dataset_path(config, config_path)
    if missing:
        raise ValueError(f"missing training config fields: {', '.join(missing)}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset does not exist: {dataset_path}")
    rows = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("messages"):
                raise ValueError(f"invalid SFT row at {dataset_path}:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError(f"SFT dataset is empty: {dataset_path}")
    return {
        "dataset_path": str(dataset_path),
        "rows": len(rows),
        "base_model": str(config["base_model"]),
        "output_dir": str(config["output_dir"]),
        "seed": int(config.get("seed", 42)),
    }


def train(config: dict[str, Any], config_path: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    summary = validate_config(config, config_path)
    if dry_run:
        return summary
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

    dataset = load_dataset("json", data_files=summary["dataset_path"], split="train")
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
    import torch

    use_bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
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
        bf16=use_bf16,
        fp16=bool(torch.cuda.is_available() and not use_bf16),
        report_to="none",
        remove_unused_columns=False,
    )
    trainer = Trainer(model=model, args=args, train_dataset=tokenized, tokenizer=tokenizer)
    trainer.train()
    trainer.save_model(config["output_dir"])
    tokenizer.save_pretrained(config["output_dir"])
    Path(config["output_dir"]).mkdir(parents=True, exist_ok=True)
    (Path(config["output_dir"]) / "training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QLoRA SFT")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="validate data/config without importing GPU training libraries")
    args = parser.parse_args()
    summary = train(load_config(args.config), args.config, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
