"""Optional DPO training entrypoint for validated preference pairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithm.training.sft.train_qlora import resolve_dataset_path


def validate_config(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    required = ["base_model", "dataset_path", "output_dir"]
    missing = [key for key in required if not config.get(key)]
    dataset_path = resolve_dataset_path(config, config_path)
    if missing:
        raise ValueError(f"missing DPO config fields: {', '.join(missing)}")
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset does not exist: {dataset_path}")
    rows = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not all(str(row.get(key) or "").strip() for key in ("prompt", "chosen", "rejected")):
                raise ValueError(f"invalid preference row at {dataset_path}:{line_number}")
            if str(row["chosen"]).strip() == str(row["rejected"]).strip():
                raise ValueError(f"chosen/rejected are identical at {dataset_path}:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError(f"DPO dataset is empty: {dataset_path}")
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
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from trl import DPOTrainer
    except ImportError as exc:
        raise RuntimeError(
            "DPO dependencies are optional. Install algorithm/training/requirements-training.txt on AutoDL."
        ) from exc

    dataset = load_dataset("json", data_files=summary["dataset_path"], split="train")
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
    Path(config["output_dir"]).mkdir(parents=True, exist_ok=True)
    (Path(config["output_dir"]) / "training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DPO on validated preference pairs")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="validate data/config without importing GPU training libraries")
    args = parser.parse_args()
    summary = train(json.loads(args.config.read_text(encoding="utf-8")), args.config, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
