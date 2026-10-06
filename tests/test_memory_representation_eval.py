import json

import pytest

from algorithm.evaluation.memory_representation_eval import (
    REVISION,
    VARIANTS,
    cases,
    prepare,
    score,
)


def oracle_rows():
    return [
        {
            "case_id": case["case_id"],
            "variant": variant,
            "revision": REVISION,
            "response": json.dumps({"facts": case["expected"]}),
        }
        for case in cases()
        for variant in VARIANTS
    ]


def test_prepared_pairs_keep_gold_out_and_change_only_representation():
    rows = prepare()
    assert len(rows) == 16
    assert len({(r["case_id"], r["variant"]) for r in rows}) == 16
    assert all("expected" not in json.loads(r["user"]) for r in rows)
    assert all(r["system"] == rows[0]["system"] for r in rows)
    changed = [r for r in rows if r["representation_changed"]]
    assert changed
    baseline = {r["case_id"]: r for r in rows if r["variant"] == "compact_full"}
    for row in changed:
        assert row["input_bytes"] < baseline[row["case_id"]]["input_bytes"]


def test_oracle_checks_scorer_not_model_quality():
    report = score(oracle_rows())
    assert all(s["exact"] == 8 for s in report["summary"].values())


def test_schema_diagnostic_only_changes_shared_system_instruction_and_revision():
    original, amended = prepare(), prepare(schema_example=True)
    for first, second in zip(original, amended, strict=True):
        assert first["user"] == second["user"]
        assert first["case_id"] == second["case_id"]
        assert second["system"].startswith(first["system"])
        assert second["revision"] == REVISION + "-schema"
    rows = oracle_rows()
    for row in rows:
        row["revision"] = REVISION + "-schema"
    assert score(rows, revision=REVISION + "-schema")["summary"]["compact_full"]["exact"] == 8
    with pytest.raises(ValueError):
        score(rows)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "revision"])
def test_reject_incomplete_or_mismatched_predictions(change):
    rows = oracle_rows()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "unknown":
        rows[0]["case_id"] = "foreign"
    else:
        rows[0]["revision"] = "wrong"
    with pytest.raises(ValueError):
        score(rows)


@pytest.mark.parametrize("response", ["[]", '{"facts":{}}', "not json", '{"facts":null}'])
def test_malformed_answer_is_failure_not_excluded(response):
    rows = oracle_rows()
    rows[0]["response"] = response
    report = score(rows)
    assert report["summary"]["compact_full"]["total"] == 8
    assert report["summary"]["compact_full"]["exact"] == 7


def test_right_value_wrong_source_and_risk_violation_are_visible():
    rows = oracle_rows()
    rows[0]["response"] = json.dumps(
        {"facts": {"current_weight": {"value": "76公斤", "source_ids": ["c-old"]}}}
    )
    risk = next(
        r for r in rows if r["case_id"] == "risk_constraint" and r["variant"] == "referenced"
    )
    risk["response"] = json.dumps(
        {"facts": {"allowed_activity": {"value": "跑步", "source_ids": ["r-pref"]}}}
    )
    report = score(rows)
    assert report["paired"]["recovered"] == ["correction"]
    assert report["paired"]["regressed"] == ["risk_constraint"]
    scored = report["cases"]
    assert scored[0]["values_correct"] and not scored[0]["sources_correct"]
    assert not next(
        r for r in scored if r["case_id"] == "risk_constraint" and r["variant"] == "referenced"
    )["critical_correct"]
