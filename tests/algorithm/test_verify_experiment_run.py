import hashlib
import json

from algorithm.training.verify_experiment_run import verify_run


def _write_json(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _completed_run(tmp_path):
    run = tmp_path / "run"
    records = run / "records"
    adapter = run / "adapter"
    records.mkdir(parents=True)
    adapter.mkdir()
    adapter_file = adapter / "adapter_model.safetensors"
    adapter_file.write_bytes(b"adapter")
    digest = hashlib.sha256(b"adapter").hexdigest()
    identity = {
        "run_id": "smoke-1",
        "dataset_version": "intent-v1",
        "train_sha256": "train",
        "eval_sha256": "eval",
        "seed": 42,
    }
    _write_json(records / "run_manifest.json", identity)
    _write_json(records / "run_summary.json", identity)
    _write_json(records / "status.json", {"status": "completed-technical"})
    _write_json(
        records / "output_manifest.json",
        {"files": [{"path": adapter_file.name, "bytes": 7, "sha256": digest}]},
    )
    _write_json(records / "command.json", {"argv": ["python", "train"]})
    _write_json(records / "environment.json", {"python": "test"})
    _write_json(records / "sync_manifest.json", {"transfers": []})
    (records / "events.jsonl").write_text(
        json.dumps({"event": "completed-technical"}) + "\n", encoding="utf-8"
    )
    (records / "metrics.jsonl").write_text(json.dumps({"eval_loss": 0.5}) + "\n", encoding="utf-8")
    (records / "resource_usage.jsonl").write_text(
        json.dumps({"gpu_peak_memory_bytes": 1}) + "\n", encoding="utf-8"
    )
    (records / "nvidia_smi.txt").write_text("gpu", encoding="utf-8")
    (records / "pip_freeze.txt").write_text("torch==test", encoding="utf-8")
    (records / "run.log").write_text("completed", encoding="utf-8")
    return run, adapter_file


def test_verify_run_checks_records_metrics_and_output_hashes(tmp_path):
    run, _ = _completed_run(tmp_path)
    report = verify_run(run)
    assert report["verified"] is True
    assert (run / "records" / "verification_report.json").exists()


def test_verify_run_detects_adapter_corruption_and_reload_requirement(tmp_path):
    run, adapter_file = _completed_run(tmp_path)
    adapter_file.write_bytes(b"changed")
    report = verify_run(run, require_adapter_reload=True)
    assert report["verified"] is False
    assert any("checksum mismatch" in error for error in report["errors"])
    assert any("reload report" in error for error in report["errors"])
