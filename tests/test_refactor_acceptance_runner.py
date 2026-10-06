from scripts.run_refactor_acceptance import CASES, evaluate, read_results


def test_manifest_preserves_all_original_numbers():
    assert [number for number, _, _ in CASES] == list(range(1, 19))
    assert all(checks for _, _, checks in CASES)


def test_missing_failed_and_skipped_checks_never_pass():
    prefix = "tests/test_example.py::test_case"
    assert not evaluate([prefix], {})["selected_checks_passed"]
    for status in ("failed", "skipped"):
        assert not evaluate([prefix], {prefix + "[first]": status})["selected_checks_passed"]
    assert evaluate([prefix], {prefix + "[first]": "passed"})["selected_checks_passed"]
    assert not evaluate([prefix], {prefix + "_similar": "passed"})["selected_checks_passed"]


def test_xml_failure_error_and_skip_are_distinct_from_success(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.test_example" name="test_case[a]" />'
        '<testcase classname="tests.test_example" name="test_case[b]"><error /></testcase>'
        '<testcase classname="tests.test_example" name="test_case[c]"><skipped /></testcase></testsuite>',
        encoding="utf-8",
    )
    results = read_results(path)
    assert list(results.values()) == ["passed", "failed", "skipped"]
    assert not evaluate(["tests/test_example.py::test_case"], results)["selected_checks_passed"]
