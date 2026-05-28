import json
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fast_api.app.core.config import get_settings


class AgentRunLogger:
    """Collect runtime trace events and write readable per-run logs."""

    SECRET_KEYS = {"api_key", "authorization", "token", "password", "secret"}

    def __init__(
        self,
        run_type: str,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        request_id: str | None = None,
        log_dir: str | None = None,
    ):
        settings = get_settings()
        self.run_type = run_type
        self.user_id = str(user_id)
        self.session_id = str(session_id) if session_id else None
        self.request_id = request_id or str(uuid.uuid4())
        self.started_at = datetime.utcnow()
        self.events: list[dict[str, Any]] = []
        self.log_dir = Path(log_dir or settings.agent_log_dir)

    def node(
        self,
        name: str,
        start: float,
        output: dict[str, Any] | None = None,
        input_summary: dict[str, Any] | None = None,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "node": name,
            "event_id": str(uuid.uuid4()),
            "request_id": self.request_id,
            "status": status,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "timestamp_utc": datetime.utcnow().isoformat(),
            "input_summary": self._sanitize(input_summary or {}),
            "output": self._sanitize(output or {}),
            "error": error,
        }
        self.events.append(event)
        return event

    def event(self, name: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        item = {
            "node": name,
            "event_id": str(uuid.uuid4()),
            "request_id": self.request_id,
            "status": "completed",
            "latency_ms": 0,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "input_summary": {},
            "output": self._sanitize(details or {}),
            "error": None,
        }
        self.events.append(item)
        return item

    def write_run_log(
        self,
        run_id: uuid.UUID | str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> str:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.started_at.strftime("%Y%m%d-%H%M%S")
        path = self.log_dir / f"{timestamp}-{run_id}.log"
        payload = {
            "run_id": str(run_id),
            "request_id": self.request_id,
            "run_type": self.run_type,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": status,
            "started_at_utc": self.started_at.isoformat(),
            "completed_at_utc": datetime.utcnow().isoformat(),
            "summary": summary,
            "error": error,
            "nodes": self.events,
        }
        with path.open("w", encoding="utf-8") as file:
            file.write(f"AI Fitness Agent run: {run_id}\n")
            file.write(f"request_id: {self.request_id}\n")
            file.write(f"run_type: {self.run_type}\n")
            file.write(f"user_id: {self.user_id}\n")
            file.write(f"session_id: {self.session_id}\n")
            file.write(f"status: {status}\n")
            file.write("\nTimeline\n")
            for event in self.events:
                file.write(
                    f"- {event['node']} | {event['status']} | "
                    f"{event['latency_ms']}ms | {event['timestamp_utc']}\n"
                )
                if event.get("input_summary"):
                    file.write(
                        "  input: "
                        + json.dumps(event["input_summary"], ensure_ascii=False, default=str)
                        + "\n"
                    )
                if event.get("output"):
                    file.write(
                        "  output: "
                        + json.dumps(event["output"], ensure_ascii=False, default=str)
                        + "\n"
                    )
                if event.get("error"):
                    file.write(f"  error: {event['error']}\n")
            file.write("\nFull JSON\n")
            file.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            file.write("\n")
        return str(path)

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(secret in lowered for secret in self.SECRET_KEYS):
                    sanitized[key] = "[REDACTED]"
                else:
                    sanitized[key] = self._sanitize(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value
