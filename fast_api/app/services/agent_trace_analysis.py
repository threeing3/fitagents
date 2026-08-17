"""Sanitized, explainable projections of persisted Agent runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fast_api.app.db import models

PHASE_BY_NODE = {
    "RequestReceived": "understand",
    "IntentRouter": "understand",
    "IntentDecisionEngine": "understand",
    "RuntimeRouter": "understand",
    "LLMPlanner": "plan",
    "PlannerFallback": "plan",
    "PlannerVerifier": "plan",
    "AgentPlanner": "plan",
    "ContextBuilder": "retrieve",
    "KnowledgeRetrieval": "retrieve",
    "DecisionRules": "retrieve",
    "TemplateSelector": "retrieve",
    "MemoryAgent": "retrieve",
    "MemoryVerifier": "verify",
    "ToolExecutor": "execute",
    "ToolSkipped": "execute",
    "PlanVerifier": "verify",
    "ResponseVerifier": "verify",
    "PlanRepair": "verify",
    "ResponseRepair": "verify",
    "GuardrailCheck": "safety",
    "CoachLLM": "respond",
    "ResponsePersisted": "respond",
    "RuntimeError": "respond",
}

PHASE_LABELS = {
    "understand": "理解请求",
    "plan": "制定计划",
    "retrieve": "召回上下文",
    "execute": "执行工具",
    "verify": "验证结果",
    "safety": "安全检查",
    "respond": "生成回复",
}


def _output(node: dict[str, Any]) -> dict[str, Any]:
    value = node.get("output")
    return value if isinstance(value, dict) else {}


def _list(value: Any, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:100] for item in value[:limit] if isinstance(item, (str, int, float))]


def _summary(node_name: str, output: dict[str, Any]) -> str:
    if node_name in {
        "IntentRouter",
        "IntentDecisionEngine",
        "RuntimeRouter",
        "LLMPlanner",
        "PlannerVerifier",
        "AgentPlanner",
    }:
        nested = (
            output.get("intent_decision") if isinstance(output.get("intent_decision"), dict) else {}
        )
        intent = (
            output.get("intent") or output.get("primary_intent") or nested.get("primary_intent")
        )
        tools = _list(output.get("tool_order"))
        if not tools and isinstance(output.get("steps"), list):
            tools = [
                str(step.get("tool_name"))
                for step in output["steps"][:6]
                if isinstance(step, dict) and step.get("tool_name")
            ]
        parts = [f"意图 {intent}" if intent else "已完成路由"]
        if tools:
            parts.append("工具顺序 " + " → ".join(tools))
        return "；".join(parts)
    if node_name == "ContextBuilder":
        return (
            f"召回记忆 {int(output.get('relevant_memory_count') or 0)} 条，"
            f"风险提示 {int(output.get('active_risk_count') or 0)} 条"
        )
    if node_name == "KnowledgeRetrieval":
        knowledge = _list(output.get("matched_knowledge_ids"))
        cases = _list(output.get("matched_case_ids"))
        return f"知识 {len(knowledge)} 条，案例 {len(cases)} 条"
    if node_name == "DecisionRules":
        return f"命中规则 {len(_list(output.get('matched_rule_ids')))} 条"
    if node_name == "ToolExecutor":
        tool_name = output.get("tool_name") or "unknown"
        status = output.get("status") or "unknown"
        attempts = int(output.get("attempts") or 1)
        return f"{tool_name} · {status} · {attempts} 次尝试"
    if node_name in {"PlanVerifier", "ResponseVerifier", "MemoryVerifier"}:
        passed = output.get("passed")
        issue_count = int(output.get("issue_count") or 0)
        return f"{'通过' if passed is not False else '未通过'} · {issue_count} 个问题"
    if node_name in {"PlanRepair", "ResponseRepair"}:
        return f"修复动作 {len(_list(output.get('repair_actions')))} 项"
    if node_name == "GuardrailCheck":
        return (
            f"动作 {output.get('action') or 'pass'} · 标记 {int(output.get('flag_count') or 0)} 项"
        )
    if node_name == "CoachLLM":
        return f"模式 {output.get('mode') or 'coaching'} · 回复 {int(output.get('response_chars') or 0)} 字符"
    if node_name == "PlannerFallback":
        return f"规划器降级：{str(output.get('reason') or '未提供原因')[:120]}"
    if node_name == "RuntimeError":
        return "运行时发生错误，系统进入降级路径"
    return str(output.get("summary") or output.get("status") or "已完成")[:160]


def _finding(
    code: str,
    severity: str,
    title: str,
    detail: str,
    node: str,
) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "detail": detail, "node": node}


def analyze_agent_run(
    run: models.AgentRun,
    tool_calls: list[models.ToolCall] | None = None,
    *,
    include_timeline: bool = True,
) -> dict[str, Any]:
    """Project a stored run into a redacted decision timeline and findings."""

    nodes = run.nodes if isinstance(run.nodes, list) else []
    timeline: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    intent: str | None = None
    planner_mode: str | None = None
    memory_count = 0
    knowledge_count = 0
    guardrail_action = "pass"
    verifier_issues = 0

    for index, raw_node in enumerate(nodes):
        if not isinstance(raw_node, dict):
            continue
        node_name = str(raw_node.get("node") or raw_node.get("event_type") or "Unknown")
        output = _output(raw_node)
        phase = PHASE_BY_NODE.get(node_name, "execute")
        status = str(raw_node.get("status") or output.get("status") or "completed")
        latency_ms = max(0, int(raw_node.get("latency_ms") or output.get("latency_ms") or 0))

        if output.get("intent") or output.get("primary_intent"):
            intent = str(output.get("intent") or output.get("primary_intent"))
        if output.get("planner_mode"):
            planner_mode = str(output["planner_mode"])
        if node_name == "ContextBuilder":
            memory_count = int(output.get("relevant_memory_count") or 0)
        if node_name == "KnowledgeRetrieval":
            knowledge_count = len(_list(output.get("matched_knowledge_ids"), limit=20))
        if node_name == "GuardrailCheck":
            guardrail_action = str(output.get("action") or "pass")
            if guardrail_action in {"warn", "block", "failed"}:
                findings.append(
                    _finding(
                        "guardrail_intervention",
                        "high" if guardrail_action in {"block", "failed"} else "medium",
                        "安全护栏介入",
                        f"护栏动作：{guardrail_action}",
                        node_name,
                    )
                )
        if node_name == "PlannerFallback" or output.get("planner_fallback") is True:
            findings.append(
                _finding(
                    "planner_fallback",
                    "medium",
                    "规划器发生降级",
                    str(output.get("reason") or "使用确定性规划器完成请求")[:160],
                    node_name,
                )
            )
        if node_name == "ToolExecutor" and status not in {"success", "completed"}:
            findings.append(
                _finding(
                    "tool_failure",
                    "high",
                    "工具执行失败",
                    f"{output.get('tool_name') or 'unknown'} 返回 {status}",
                    node_name,
                )
            )
        if node_name in {"PlanVerifier", "ResponseVerifier", "MemoryVerifier"}:
            issues = int(output.get("issue_count") or 0)
            verifier_issues += issues
            if output.get("passed") is False or issues:
                findings.append(
                    _finding(
                        "verifier_issue",
                        "medium",
                        "验证器发现问题",
                        f"{node_name} 发现 {issues} 个问题",
                        node_name,
                    )
                )
        knowledge_debug = output.get("knowledge_debug")
        if (
            isinstance(knowledge_debug, dict)
            and knowledge_debug.get("vector_status") == "vector unavailable"
        ):
            findings.append(
                _finding(
                    "retrieval_degraded",
                    "low",
                    "语义向量不可用",
                    "本次检索使用 BM25 确定性降级",
                    node_name,
                )
            )
        if node_name == "RuntimeError" or status in {"failed", "error"}:
            findings.append(
                _finding(
                    "runtime_error",
                    "high",
                    "运行路径发生错误",
                    "系统记录了错误并尝试确定性降级",
                    node_name,
                )
            )

        if include_timeline:
            timeline.append(
                {
                    "order": index + 1,
                    "node": node_name,
                    "phase": phase,
                    "phase_label": PHASE_LABELS[phase],
                    "status": status,
                    "latency_ms": latency_ms,
                    "summary": _summary(node_name, output),
                }
            )

    calls = tool_calls or []
    tool_names = [call.tool_name for call in calls[:12]]
    if not findings:
        findings.append(
            _finding(
                "healthy_run",
                "info",
                "未发现运行异常",
                "规划、工具、验证和安全节点未报告可诊断失败",
                "AgentRun",
            )
        )

    completed_at = run.completed_at.isoformat() if isinstance(run.completed_at, datetime) else None
    return {
        "run_id": str(run.id),
        "run_type": run.run_type,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "completed_at": completed_at,
        "summary": (run.summary or "")[:180],
        "node_count": len(timeline) if include_timeline else len(nodes),
        "tool_count": len(calls),
        "total_latency_ms": sum(item["latency_ms"] for item in timeline),
        "decision": {
            "intent": intent or "unknown",
            "planner_mode": planner_mode or "unknown",
            "memory_count": memory_count,
            "knowledge_count": knowledge_count,
            "tool_names": tool_names,
            "verifier_issue_count": verifier_issues,
            "guardrail_action": guardrail_action,
        },
        "findings": findings[:12],
        "timeline": timeline,
        "privacy": "sanitized_projection_no_raw_user_context",
    }
