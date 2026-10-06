from algorithm.evaluation.memory_representation_local_run import (
    ENDPOINT,
    ordered_inputs,
    request_body,
)


def test_local_only_and_counterbalanced_frozen_pairs():
    assert ENDPOINT == "http://127.0.0.1:8079/v1/chat/completions"
    rows = ordered_inputs()
    assert len(rows) == 16
    for i in range(0, 16, 2):
        assert rows[i]["case_id"] == rows[i + 1]["case_id"]
        assert rows[i]["variant"] != rows[i + 1]["variant"]
        assert rows[i]["variant"] == ("compact_full" if (i // 2) % 2 == 0 else "referenced")
        first, second = request_body(rows[i]), request_body(rows[i + 1])
        assert first["messages"][0] == second["messages"][0]
        assert {k: v for k, v in first.items() if k != "messages"} == {
            k: v for k, v in second.items() if k != "messages"
        }
        assert "expected" not in first and "expected" not in second
