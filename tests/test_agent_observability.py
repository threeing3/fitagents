import json
import time
from pathlib import Path
from uuid import uuid4

from fast_api.app.services.agent_observability import AgentRunLogger


def test_agent_run_logger_redacts_secrets_and_writes_readable_log(tmp_path):
    logger = AgentRunLogger(
        run_type="chat",
        user_id=uuid4(),
        session_id=uuid4(),
        log_dir=str(tmp_path),
    )

    start = time.perf_counter()
    event = logger.node(
        "CoachLLM",
        start,
        output={"api_key": "secret", "answer": "ok"},
        input_summary={"message": "hello"},
    )
    path = logger.write_run_log(uuid4(), "completed", "ok")
    content = Path(path).read_text(encoding="utf-8")

    assert event["output"]["api_key"] == "[REDACTED]"
    assert "AI 私教 Agent 运行日志" in content
    assert "一、执行时间线" in content
    assert "输入摘要" in content
    assert "输出摘要" in content
    assert "二、完整 JSON（用于深度调试）" in content
    assert "CoachLLM" in content
    assert "secret" not in content
    json_payload = content.split("二、完整 JSON（用于深度调试）", 1)[1].split("\n", 2)[2]
    assert json.loads(json_payload)["status"] == "completed"
