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
    assert "Timeline" in content
    assert "CoachLLM" in content
    assert "secret" not in content
    assert json.loads(content.split("Full JSON", 1)[1])["status"] == "completed"
