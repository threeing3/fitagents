"""Reproducible Qwen3 QLoRA training with assistant-only loss.

GPU libraries are imported lazily so application and CI environments can run
the data/config preflight without downloading a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(raw: str, config_path: Path | None = None) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path]
    if config_path is not None:
        candidates.extend(parent / path for parent in config_path.parents)
    return next((item.resolve() for item in candidates if item.exists()), candidates[0].resolve())


def resolve_dataset_path(config: dict[str, Any], config_path: Path | None = None) -> Path:
    """Backward-compatible path resolver used by the guarded DPO entrypoint."""

    return _resolve_path(str(config["dataset_path"]), config_path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"invalid JSON object at {path}:{line_number}")
            messages = row.get("messages")
            if not isinstance(messages, list) or len(messages) < 2:
                raise ValueError(f"invalid messages at {path}:{line_number}")
            if messages[-1].get("role") != "assistant" or not messages[-1].get("content"):
                raise ValueError(
                    f"final non-empty assistant message required at {path}:{line_number}"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"SFT dataset is empty: {path}")
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_config(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    required = [
        "experiment_id",
        "base_model",
        "train_dataset_path",
        "eval_dataset_path",
        "output_root",
        "dataset_version",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"missing training config fields: {', '.join(missing)}")
    if config["base_model"] != "Qwen/Qwen3-4B":
        raise ValueError("Intent 04 base_model must be Qwen/Qwen3-4B")
    if config.get("assistant_only_loss") is not True:
        raise ValueError("assistant_only_loss must be true")
    if config.get("enable_thinking") is not False:
        raise ValueError("enable_thinking must be false for structured intent classification")
    train_path = _resolve_path(str(config["train_dataset_path"]), config_path)
    eval_path = _resolve_path(str(config["eval_dataset_path"]), config_path)
    if not train_path.exists():
        raise FileNotFoundError(f"train dataset does not exist: {train_path}")
    if not eval_path.exists():
        raise FileNotFoundError(f"eval dataset does not exist: {eval_path}")
    train_rows = _read_jsonl(train_path)
    eval_rows = _read_jsonl(eval_path)
    train_ids = {str(row.get("example_id")) for row in train_rows}
    eval_ids = {str(row.get("example_id")) for row in eval_rows}
    if train_ids & eval_ids:
        raise ValueError("train and eval datasets share example_id values")
    train_families = {str(row.get("template_family")) for row in train_rows}
    eval_families = {str(row.get("template_family")) for row in eval_rows}
    if train_families & eval_families:
        raise ValueError("train and eval datasets share template_family values")
    return {
        "experiment_id": str(config["experiment_id"]),
        "base_model": str(config["base_model"]),
        "dataset_version": str(config["dataset_version"]),
        "train_dataset_path": str(train_path),
        "eval_dataset_path": str(eval_path),
        "train_rows": len(train_rows),
        "eval_rows": len(eval_rows),
        "train_sha256": _sha256(train_path),
        "eval_sha256": _sha256(eval_path),
        "seed": int(config.get("seed", 42)),
        "assistant_only_loss": True,
    }


def tokenize_with_assistant_mask(
    tokenizer: Any, row: dict[str, Any], max_length: int
) -> dict[str, Any]:
    """Apply the model chat template and mask every non-assistant token."""

    common = {
        "tokenize": True,
        "return_dict": True,
        "truncation": True,
        "max_length": max_length,
        "enable_thinking": False,
    }
    template = str(getattr(tokenizer, "chat_template", "") or "")
    supports_native_mask = "{% generation" in template
    encoded = tokenizer.apply_chat_template(
        row["messages"],
        add_generation_prompt=False,
        return_assistant_tokens_mask=supports_native_mask,
        **common,
    )
    input_ids = list(encoded["input_ids"])
    attention_mask = list(encoded.get("attention_mask") or [1] * len(input_ids))
    if supports_native_mask:
        assistant_mask = encoded.get("assistant_masks") or encoded.get("assistant_tokens_mask")
        if assistant_mask is None:
            raise ValueError(
                "chat template declared generation blocks but returned no assistant mask"
            )
    else:
        prompt = tokenizer.apply_chat_template(
            row["messages"][:-1], add_generation_prompt=True, **common
        )
        prompt_length = len(prompt["input_ids"])
        if prompt_length >= len(input_ids):
            raise ValueError("assistant boundary is missing or truncated")
        assistant_mask = [0] * prompt_length + [1] * (len(input_ids) - prompt_length)
    labels = [token if int(mask) else -100 for token, mask in zip(input_ids, assistant_mask)]
    if not any(label != -100 for label in labels):
        raise ValueError("assistant response has no trainable tokens after truncation")
    return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}


def _adapter_manifest(output_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {"files": files, "file_count": len(files)}


def _write_environment(records_dir: Path) -> None:
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
    }
    (records_dir / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=False
    )
    (records_dir / "pip_freeze.txt").write_text(freeze.stdout, encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _set_status(records_dir: Path, status: str, **extra: Any) -> None:
    payload = {"status": status, "updated_at": _now(), **extra}
    (records_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    with (records_dir / "run.log").open("a", encoding="utf-8") as handle:
        handle.write(
            f"{_now()} status={status} {json.dumps(extra, ensure_ascii=False, default=str)}\n"
        )
    _append_jsonl(records_dir / "events.jsonl", {"timestamp": _now(), "event": status, **extra})


def train(
    config: dict[str, Any],
    config_path: Path | None = None,
    *,
    run_id: str | None = None,
    dry_run: bool = False,
    resume_from_checkpoint: str | None = None,
) -> dict[str, Any]:
    summary = validate_config(config, config_path)
    if dry_run:
        return summary
    if not run_id:
        raise ValueError("run_id is required for a real training run")
    output_root = _resolve_path(str(config["output_root"]), config_path)
    run_dir = output_root / run_id
    records_dir = run_dir / "records"
    if not (records_dir / "command.json").exists():
        raise RuntimeError("prepare the immutable run before training")
    output_dir = run_dir / "adapter"
    if output_dir.exists() and not resume_from_checkpoint:
        raise FileExistsError(f"adapter output already exists: {output_dir}")
    _set_status(records_dir, "running")
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            DataCollatorForSeq2Seq,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Install algorithm/training/requirements-training.txt in the isolated GPU environment."
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError("QLoRA training requires an available CUDA GPU")
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_environment(records_dir)

    tokenizer = AutoTokenizer.from_pretrained(config["base_model"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_dataset = load_dataset("json", data_files=summary["train_dataset_path"], split="train")
    eval_dataset = load_dataset("json", data_files=summary["eval_dataset_path"], split="train")
    run_manifest = json.loads((records_dir / "run_manifest.json").read_text(encoding="utf-8"))
    row_limit = run_manifest.get("row_limit")
    if isinstance(row_limit, int) and row_limit > 0:
        train_dataset = train_dataset.select(range(min(row_limit, len(train_dataset))))
    max_length = int(config.get("max_seq_length", 2048))

    def tokenize(row: dict[str, Any]) -> dict[str, Any]:
        return tokenize_with_assistant_mask(tokenizer, row, max_length)

    train_dataset = train_dataset.map(tokenize, remove_columns=train_dataset.column_names)
    eval_dataset = eval_dataset.map(tokenize, remove_columns=eval_dataset.column_names)
    use_bf16 = bool(torch.cuda.is_bf16_supported())
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config["base_model"], quantization_config=quantization, device_map="auto"
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=bool(config.get("gradient_checkpointing", True))
    )
    model = get_peft_model(
        model,
        LoraConfig(
            r=int(config.get("lora_r", 16)),
            lora_alpha=int(config.get("lora_alpha", 32)),
            lora_dropout=float(config.get("lora_dropout", 0.05)),
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=list(config["lora_target_modules"]),
        ),
    )
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=float(config.get("num_train_epochs", 2)),
        per_device_train_batch_size=int(config.get("per_device_train_batch_size", 1)),
        per_device_eval_batch_size=int(config.get("per_device_eval_batch_size", 1)),
        gradient_accumulation_steps=int(config.get("gradient_accumulation_steps", 8)),
        gradient_checkpointing=bool(config.get("gradient_checkpointing", True)),
        learning_rate=float(config.get("learning_rate", 2e-4)),
        warmup_ratio=float(config.get("warmup_ratio", 0.05)),
        logging_steps=int(config.get("logging_steps", 5)),
        eval_strategy="steps",
        eval_steps=int(config.get("eval_steps", 25)),
        save_strategy="steps",
        save_steps=int(config.get("save_steps", 25)),
        save_total_limit=int(config.get("save_total_limit", 2)),
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        seed=int(config.get("seed", 42)),
        data_seed=int(config.get("seed", 42)),
        bf16=use_bf16,
        fp16=not use_bf16,
        report_to="none",
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=DataCollatorForSeq2Seq(tokenizer=tokenizer, padding=True),
        processing_class=tokenizer,
    )
    result = trainer.train(resume_from_checkpoint=resume_from_checkpoint)
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    summary.update(
        {
            "run_id": run_id,
            "output_dir": str(output_dir),
            "train_metrics": result.metrics,
            "eval_metrics": trainer.evaluate(),
            "effective_train_rows": len(train_dataset),
        }
    )
    (records_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (records_dir / "output_manifest.json").write_text(
        json.dumps(_adapter_manifest(output_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for metric in trainer.state.log_history:
        _append_jsonl(records_dir / "metrics.jsonl", {"timestamp": _now(), **metric})
    disk = shutil.disk_usage(run_dir)
    _append_jsonl(
        records_dir / "resource_usage.jsonl",
        {
            "timestamp": _now(),
            "event": "training_completed",
            "gpu_peak_memory_bytes": int(torch.cuda.max_memory_allocated()),
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
        },
    )
    _set_status(
        records_dir,
        "completed-technical",
        train_loss=result.metrics.get("train_loss"),
        eval_loss=summary["eval_metrics"].get("eval_loss"),
    )
    return summary


def _record_cli_failure(
    config: dict[str, Any], config_path: Path, run_id: str | None, exc: Exception
) -> None:
    if not run_id:
        return
    records = _resolve_path(str(config.get("output_root", "")), config_path) / run_id / "records"
    if records.exists():
        _set_status(records, "failed", error_type=type(exc).__name__, error=str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Qwen3-4B assistant-only QLoRA SFT")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--resume-from-checkpoint")
    parser.add_argument(
        "--dry-run", action="store_true", help="validate data/config without loading GPU libraries"
    )
    args = parser.parse_args()
    config = load_config(args.config)
    try:
        summary = train(
            config,
            args.config,
            run_id=args.run_id,
            dry_run=args.dry_run,
            resume_from_checkpoint=args.resume_from_checkpoint,
        )
    except Exception as exc:
        _record_cli_failure(config, args.config, args.run_id, exc)
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
