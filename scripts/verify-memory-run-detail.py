"""End-to-end smoke test for memory.verify and agent run detail.

The script writes a readable Chinese experiment log under logs/experiments.
It intentionally redacts tokens and does not print secrets.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, request


BASE_URL = "http://localhost:1015"
ROOT = Path(__file__).resolve().parents[1]


def request_json(method: str, path: str, data: dict | None = None, token: str | None = None, timeout: int = 240):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = request.Request(BASE_URL + path, data=body, method=method, headers=headers)
    return request.urlopen(req, timeout=timeout)


def main() -> int:
    log_dir = ROOT / "logs" / "experiments"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{stamp}-memory-verify-run-detail.log"
    lines: list[str] = ["# Memory Verify + Run Detail Debug 端到端验证日志\n"]

    def log(title: str, payload=None) -> None:
        lines.append(f"\n## {title}\n")
        if payload is None:
            return
        if isinstance(payload, (dict, list)):
            lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            lines.append(str(payload))
        lines.append("\n")

    try:
        log("实验目标", "验证真实对话链路中 memory.verify、MemoryVerifier 节点、Run Detail Debug 数据、agent run 可读日志是否完整产生。")
        log("实验步骤", "注册用户 -> 创建会话 -> 发送流式消息 -> 读取 run detail -> 检查节点、工具调用和日志路径。")

        suffix = uuid.uuid4().hex[:10]
        with request_json(
            "POST",
            "/v1/auth/register",
            {
                "email": f"memory-verify-{suffix}@example.com",
                "password": "Passw0rd!",
                "display_name": "Memory Verify Smoke",
                "username": f"memverify-{suffix}",
            },
        ) as resp:
            auth = json.loads(resp.read().decode("utf-8"))
        token = auth["access_token"]
        user_id = auth["user_id"]
        log("1. 注册用户", {"user_id": user_id, "email": auth["email"], "token": "[REDACTED]"})

        with request_json(
            "POST",
            "/v1/chat/sessions",
            {"display_name": "Memory Verify Smoke", "title": "Memory Verify Smoke"},
            token=token,
        ) as resp:
            session = json.loads(resp.read().decode("utf-8"))
        session_id = session["session_id"]
        log("2. 创建会话", session)

        message = (
            "我是男生，21岁，178cm，80kg，目标12周健康减脂，每周练5天，"
            "系统训练过1年左右，在健身房锻炼。我的右肩没有伤，我平时不自己做饭，甲亢在吃赛治。"
        )
        events: list[dict] = []
        assistant_parts: list[str] = []
        run_id = None
        stream_log_path = None
        with request_json(
            "POST",
            "/v1/chat/messages/stream",
            {"session_id": session_id, "message": message},
            token=token,
        ) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                event = json.loads(raw.decode("utf-8", errors="replace"))
                events.append(event)
                if event.get("type") in {"answer_delta", "token"}:
                    assistant_parts.append(event.get("text") or event.get("content") or event.get("delta") or "")
                if event.get("type") == "done":
                    run_id = event.get("run_id")
                    stream_log_path = event.get("log_path")

        assistant_text = "".join(assistant_parts)
        log(
            "3. 流式对话结果",
            {
                "message": message,
                "event_count": len(events),
                "event_types": sorted({str(event.get("type")) for event in events}),
                "run_id": run_id,
                "stream_log_path": stream_log_path,
                "assistant_preview": assistant_text[:1000],
            },
        )
        if not run_id:
            raise RuntimeError("stream did not return run_id")

        with request_json("GET", f"/v1/agent-runs/{run_id}", token=token) as resp:
            detail = json.loads(resp.read().decode("utf-8"))
        node_names = [node.get("node") for node in detail.get("nodes", [])]
        tool_names = [tool.get("tool_name") for tool in detail.get("tool_calls", [])]
        memory_node = next((node for node in detail.get("nodes", []) if node.get("node") == "MemoryVerifier"), None)
        memory_output = (memory_node or {}).get("output") or {}
        assertions = {
            "存在 MemoryVerifier 节点": "MemoryVerifier" in node_names,
            "存在 memory.verify 工具调用": "memory.verify" in tool_names,
            "存在 memory.write 工具调用": "memory.write" in tool_names,
            "Run Detail 返回日志路径": bool(detail.get("log_path")),
            "MemoryVerifier 输出 passed 字段": "passed" in memory_output,
        }
        log(
            "4. Run Detail Debug 检查",
            {
                "run_id": detail.get("id"),
                "status": detail.get("status"),
                "node_count": len(node_names),
                "tool_count": len(tool_names),
                "node_names": node_names,
                "tool_names": tool_names,
                "log_path": detail.get("log_path"),
                "assertions": assertions,
                "memory_verify_output": memory_output,
            },
        )

        try:
            with request_json("GET", f"/v1/users/{user_id}/dashboard", token=token) as resp:
                dashboard = json.loads(resp.read().decode("utf-8"))
            dashboard_payload = {
                "status": "ok",
                "profile": dashboard.get("profile"),
                "recent_memories": (dashboard.get("recent_memories") or [])[:10],
            }
        except error.HTTPError as exc:
            dashboard_payload = {
                "status": "diagnostic_failed",
                "http_status": exc.code,
                "note": "Dashboard 不是本脚本的核心验收项；这里仅记录附加诊断结果，不阻断 memory.verify 和 run detail 验证。",
            }
        log("5. Dashboard 附加诊断", dashboard_payload)

        if not all(assertions.values()):
            raise RuntimeError(json.dumps(assertions, ensure_ascii=False))
        log("最终结论", "通过：memory.verify 已进入真实聊天工具链；Run Detail 可读取 MemoryVerifier 节点和工具调用；agent run 日志路径已持久化。")
        print(log_path)
        return 0
    finally:
        log_path.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
