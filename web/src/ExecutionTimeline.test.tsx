import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExecutionTimeline, foldSubagentRows, publicTraceMetadata, traceToExecutionRows } from "./ExecutionTimeline";
import type { AgentTraceItem, ExecutionEvent } from "./types";

const event = (status: ExecutionEvent["status"], summary: string, details = {}): ExecutionEvent => ({
  type: "execution_event", name: "test", status, source: "rule", summary,
  recorded_at: "2026-10-03T02:00:00Z", details,
});

it("folds child updates by identity, retaining failure and separate domains", () => {
  const rows = [
    { id: "a", name: "subagent.started", summary: "训练开始", status: "running" as const, details: { child_id: "training" } },
    { id: "b", name: "subagent.result", summary: "训练失败", status: "failed" as const, details: { child_id: "training" } },
    { id: "c", name: "subagent.result", summary: "饮食完成", status: "completed" as const, details: { child_id: "nutrition" } },
  ];
  expect(foldSubagentRows(rows)).toHaveLength(2);
  expect(foldSubagentRows(rows)[0].status).toBe("failed");
});

it("does not leave a completed child's running event as an unresolved warning", () => {
  render(<ExecutionTimeline events={[
    { ...event("running", "训练查询中", { child_id: "one", domain: "training", label: "训练" }), name: "subagent.started" },
    { ...event("completed", "训练已返回", { child_id: "one", domain: "training", label: "训练" }), name: "subagent.result" },
  ]} />);
  expect(document.querySelector(".execution-preview")).toBeNull();
  expect(document.querySelectorAll(".execution-timeline > ol > li")).toHaveLength(1);
});
const step = (id: string, status?: string, callId?: string): AgentTraceItem => ({
  id, type: "tool_call", title: "unfamiliar.tool", summary: "读取训练记录",
  metadata: { status, ...(callId ? { call_id: callId } : {}) },
});

describe("compact execution presentation", () => {
  it("folds successful history and raw parameters by default", () => {
    render(<ExecutionTimeline events={[event("completed", "已读取档案", { synthetic: true }), event("completed", "回复已保存")]} />);
    expect(screen.getByText("回复已保存", { selector: ".execution-current span" })).toBeVisible();
    expect(screen.getByText("已读取档案")).not.toBeVisible();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("查看执行记录"));
    expect(screen.getByText("已读取档案")).toBeVisible();
    expect(screen.getByText(/不是模型内部完整思考/)).toBeVisible();
    expect(screen.getByText(/synthetic/)).not.toBeVisible();
  });

  it("keeps blocked, failed and unknown outcomes discoverable without expansion", () => {
    render(<ExecutionTimeline events={[
      event("blocked", "尚未执行"), event("failed", "写入回滚"), event("outcome_unknown", "需要核对结果"),
    ]} />);
    expect(screen.getByText("写入回滚", { selector: ".execution-preview .execution-line-summary" })).toBeVisible();
    expect(screen.getByText("结果未知", { selector: ".execution-preview .execution-line-state" })).toBeVisible();
    expect(screen.getByText(/还有 1 条待核对记录/)).toBeVisible();
    fireEvent.click(screen.getByText("查看执行记录"));
    expect(screen.getAllByText("规则判断")[0]).toBeVisible();
    expect(screen.getByText("受阻／等待授权")).toBeVisible();
  });

  it("does not imply approval has executed the change", () => {
    render(<ExecutionTimeline events={[
      { ...event("blocked", "草案等待批准", { approval_id: "one" }), name: "approval.wait" },
      { ...event("completed", "已批准，等待后台执行；计划尚未修改。", { approval_id: "one", decision: "approved" }), name: "approval.decision" },
    ]} />);
    expect(screen.getByText("已批准，等待后台执行；计划尚未修改。", { selector: ".execution-current span" })).toBeVisible();
    expect(document.querySelectorAll(".execution-preview > li")).toHaveLength(0);
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("shows at most two recent live lines while retaining all actual rows in the disclosure", () => {
    const trace = Array.from({ length: 8 }, (_, i) => step(String(i), "completed"));
    render(<ExecutionTimeline trace={trace} busy status="正在生成回复…" />);
    expect(document.querySelectorAll(".execution-preview > li")).toHaveLength(2);
    expect(document.querySelectorAll(".execution-timeline > ol > li")).toHaveLength(8);
    expect(screen.getByText("正在生成回复…")).toBeVisible();
    expect(document.querySelector(".execution-timeline")).not.toHaveAttribute("open");
  });

  it("never converts a missing result or stopped stream into success", () => {
    expect(traceToExecutionRows([step("missing")])[0].status).toBe("unknown");
    render(<ExecutionTimeline trace={[step("running", "running")]} busy={false} />);
    expect(screen.getByText("完成状态未确认", { selector: ".execution-preview .execution-line-state" })).toBeVisible();
  });

  it("pairs explicit call identities and preserves repeated calls to the same tool", () => {
    const rows = traceToExecutionRows([
      step("start-a", "running", "a"), step("end-a", "failed", "a"),
      step("start-b", "running", "b"), step("end-b", "completed", "b"),
      step("no-id-1", "completed"), step("no-id-2", "completed"),
    ]);
    expect(rows).toHaveLength(4);
    expect(rows[0].status).toBe("failed");
    expect(rows[1].status).toBe("completed");
    expect(rows[0].id).toBe("start-a");
  });

  it("preserves tool outcome over the completed event envelope", () => {
    expect(publicTraceMetadata({ status: "completed", metadata: { status: "failed", tool_name: "plan.write" } }).status).toBe("failed");
    expect(publicTraceMetadata({ status: "completed" }).status).toBe("completed");
    expect(publicTraceMetadata({}).status).toBeUndefined();
  });

  it("renders unknown tools without inventing workflow phases", () => {
    render(<ExecutionTimeline trace={[{ ...step("custom", "completed"), summary: "自定义工具结果" }]} />);
    fireEvent.click(screen.getByText("查看执行记录"));
    expect(screen.getByText("自定义工具结果", { selector: ".execution-timeline .execution-line-summary" })).toBeVisible();
    expect(screen.queryByText("规划与档案")).not.toBeInTheDocument();
  });

  it("shows analyst and planner and replaces stale completed advice with invalidation", () => {
    render(<ExecutionTimeline events={[
      { ...event("completed", "分析已交付", { child_id: "analyst", label: "证据分析" }), name: "subagent.result" },
      { ...event("completed", "规划已交付", { child_id: "planner", label: "方案规划" }), name: "subagent.result" },
      { ...event("failed", "证据已被纠正，先前建议不再有效。", { child_id: "analyst", label: "证据分析" }), name: "subagent.result" },
    ]} />);
    fireEvent.click(screen.getByText("查看执行记录"));
    expect(screen.getAllByText(/证据分析/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/方案规划/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("证据已被纠正，先前建议不再有效。").length).toBeGreaterThan(0);
    expect(document.querySelectorAll(".execution-timeline > ol > li")).toHaveLength(2);
  });

  it("keeps business steps concise and moves successful internal events to secondary audit", () => {
    const business = { ...step("business", "completed"), metadata: { step_id: "one", tool_name: "context.build", status: "completed" } };
    const internal = { ...step("internal", "completed"), type: "step" as const, title: "Registry", summary: "注册工具列表" };
    render(<ExecutionTimeline trace={[internal, business]} />);
    fireEvent.click(screen.getByText("查看执行记录"));
    expect(screen.getByText("读取训练上下文")).toBeVisible();
    expect(screen.getByText("注册工具列表")).not.toBeVisible();
    fireEvent.click(screen.getByText(/完整审计记录/));
    expect(screen.getByText("注册工具列表")).toBeVisible();
  });
});
