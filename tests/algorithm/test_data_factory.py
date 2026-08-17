import json

from algorithm.data.export_traces import export_training_examples
from algorithm.data.sample_factory import build_synthetic_examples, build_synthetic_preference_pairs
from algorithm.data.sanitize import sanitize_text
from algorithm.data.validate_dataset import validate_training_rows
from algorithm.datasets.build_bundle import build_bundle


def test_synthetic_factory_is_explicit_and_reproducible():
    first = [row.to_dict() for row in build_synthetic_examples(12, seed=7)]
    second = [row.to_dict() for row in build_synthetic_examples(12, seed=7)]
    assert first == second
    assert {row["source"] for row in first} == {"synthetic"}
    assert {row["outcome"]["label_source"] for row in first} == {"simulated_outcome"}
    assert validate_training_rows(first)["error_count"] == 0
    assert len(build_synthetic_preference_pairs(first)) == 12


def test_legacy_synthetic_bundle_is_not_sft_or_tool_training_eligible(tmp_path):
    output_dir = tmp_path / "bundle"
    report = build_bundle(None, output_dir, synthetic_count=18, seed=11)
    assert report["validation"]["error_count"] == 0
    counts = report["manifest"]["row_counts"]
    assert counts["training_examples"] == 18
    assert counts["sft"] == 0
    assert counts["tool_decisions"] == 0
    assert counts["safety"] == 3
    assert counts["preference_pairs"] == 0
    assert report["manifest"]["eligibility_counts"]["dpo_human_reviewed"] == 0
    assert report["manifest"]["eligibility_counts"]["seed_eval_training_rows"] == 0
    manifest = json.loads((output_dir / "bundle.manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_counts"] == {"synthetic": 18}
    assert any("not training eligible" in note for note in manifest["notes"])


def test_synthetic_preferences_are_explicit_opt_in(tmp_path):
    report = build_bundle(
        None,
        tmp_path / "bundle",
        synthetic_count=18,
        seed=11,
        include_synthetic_preferences=True,
    )
    assert report["manifest"]["row_counts"]["preference_pairs"] == 18
    assert report["manifest"]["eligibility_counts"]["dpo_human_reviewed"] == 0


def test_synthetic_factory_covers_every_scenario_in_each_split(tmp_path):
    report = build_bundle(None, tmp_path / "bundle", synthetic_count=120, seed=42)
    coverage = report["manifest"]["scenario_split_counts"]
    assert len(coverage) == 6
    assert all(
        set(split_counts) == {"train", "validation", "test"} for split_counts in coverage.values()
    )


def test_exporter_allows_optional_log_directory(tmp_path):
    output = tmp_path / "training_examples.jsonl"
    manifest = export_training_examples(output, db_paths=[tmp_path / "missing.db"], log_dir=None)
    assert manifest.row_counts["training_examples"] == 0
    assert output.read_text(encoding="utf-8") == ""


def test_sanitizer_redacts_common_pii_and_secrets():
    scrubbed = sanitize_text("联系 a@example.com 或 13812345678，token sk-abcdefghijklmnop")
    assert "a@example.com" not in scrubbed
    assert "13812345678" not in scrubbed
    assert "sk-abcdefghijklmnop" not in scrubbed
