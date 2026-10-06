import asyncio
import json
from io import BytesIO

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from algorithm.evaluation.local_qwen_model import LocalQwenModel
from algorithm.evaluation.tool_loop_task_eval import run


def test_transport_is_local_and_preserves_usage_and_raw_response():
    model = LocalQwenModel()
    captured = []
    raw = {
        "model": "qwen-local",
        "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }

    class Transport:
        def open(self, request, timeout):
            captured.append((request, timeout))
            return BytesIO(json.dumps(raw).encode())

    model.transport = Transport()
    result = asyncio.run(
        model.ainvoke([SystemMessage(content="rules"), HumanMessage(content="query")])
    )
    assert captured[0][0].full_url == "http://127.0.0.1:8079/v1/chat/completions"
    body = json.loads(captured[0][0].data)
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "query"},
    ]
    assert result.response_metadata["raw_response"] == raw
    assert result.usage_metadata["total_tokens"] == 15
    model.calls = 40
    with pytest.raises(RuntimeError, match="limit"):
        asyncio.run(model.ainvoke([]))
    assert len(captured) == 1


def test_reject_transport_ambiguity_before_creating_output(tmp_path):
    output = tmp_path / "ambiguous"
    with pytest.raises(ValueError):
        asyncio.run(run(output, live=True, local=True))
    assert not output.exists()
