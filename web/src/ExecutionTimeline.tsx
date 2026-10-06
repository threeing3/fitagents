import React from "react";
import type { AgentTraceItem, ExecutionEvent } from "./types";
import { useLanguage } from "./LanguageContext";

type RowStatus = ExecutionEvent["status"] | "unknown";
export type ExecutionRow = {
  id: string; name: string; summary: string; status: RowStatus;
  source?: string; recordedAt?: string; latencyMs?: number;
  details: Record<string, unknown>;
};

const statuses: Record<RowStatus, [string, string]> = {
  pending: ["等待", "Waiting"], running: ["处理中", "Running"],
  completed: ["已记录", "Recorded"], failed: ["失败", "Failed"],
  blocked: ["受阻／等待授权", "Blocked / awaiting approval"],
  skipped: ["已跳过", "Skipped"], outcome_unknown: ["结果未知", "Outcome unknown"],
  unknown: ["完成状态未确认", "Completion unconfirmed"],
};
const sources: Record<string, string> = {
  runtime: "执行控制", rule: "规则判断", tool: "工具执行",
  user: "用户决定", model_summary: "决策摘要",
};
const toolLabels: Record<string, [string, string]> = {
  "profile.extract": ["整理当前信息", "Organize current information"],
  "memory.verify": ["核对记忆候选", "Verify memory candidates"],
  "memory.write": ["保存已验证记忆", "Save verified memories"],
  "context.build": ["读取训练上下文", "Read training context"],
  "plan.decide": ["核对计划请求", "Check plan request"],
  "plan.generate": ["生成候选训练计划", "Generate candidate plan"],
  "plan.verify": ["检查训练计划", "Check training plan"],
  "plan.repair": ["修正计划约束", "Repair plan constraints"],
  "coach.reply": ["整理回复", "Prepare response"],
  "response.verify": ["检查回复约束", "Check response constraints"],
  "response.repair": ["修正回复约束", "Repair response constraints"],
  "guardrail.check": ["核对安全边界", "Check safety boundaries"],
  "response.persist": ["保存本轮记录", "Save turn record"],
};

/** The envelope can mean "event recorded", while metadata describes actual tool outcome. */
export function publicTraceMetadata(event: { metadata?: Record<string, unknown>; status?: unknown }) {
  return { ...event.metadata, status: event.metadata?.status ?? event.status };
}

function rowStatus(value: unknown): RowStatus {
  if (value === "success" || value === "done") return "completed";
  if (value === "error") return "failed";
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(statuses, value) ? value as RowStatus : "unknown";
}

/** Only an explicit call/step identity pairs lifecycle updates; tool names are not identities. */
export function traceToExecutionRows(trace: AgentTraceItem[]): ExecutionRow[] {
  const rows: ExecutionRow[] = [];
  const calls = new Map<string, number>();
  for (const item of trace) {
    if (!["step", "tool_call", "error"].includes(item.type)) continue;
    const metadata = item.metadata || {};
    const callId = metadata.call_id ?? metadata.tool_call_id;
    const identity = callId != null ? `call:${String(callId)}` : metadata.step_id != null ? `step:${String(metadata.step_id)}` : null;
    const name = String(metadata.tool_name || item.title);
    const row: ExecutionRow = {
      id: item.id, name, summary: item.summary || name,
      status: item.type === "error" ? "failed" : rowStatus(metadata.status),
      source: item.type === "tool_call" || metadata.tool_name ? "tool" : "runtime",
      latencyMs: item.latency_ms, details: metadata,
    };
    const key = identity;
    const previous = key == null ? undefined : calls.get(key);
    if (previous == null) {
      if (key != null) calls.set(key, rows.length);
      rows.push(row);
    } else {
      rows[previous] = { ...row, id: rows[previous].id };
    }
  }
  return rows;
}

function visibleStatus(row: ExecutionRow, busy: boolean): RowStatus {
  return !busy && row.status === "running" ? "unknown" : row.status;
}

/** Fold child lifecycle by explicit identity; keep read events in the audit disclosure. */
export function foldSubagentRows(rows: ExecutionRow[]): ExecutionRow[] {
  const children = new Map<string, number>();
  const folded: ExecutionRow[] = [];
  for (const row of rows) {
    const child = row.name.startsWith("subagent.") ? row.details.child_id : null;
    if (child == null) { folded.push(row); continue; }
    const key = String(child);
    const previous = children.get(key);
    if (previous == null) { children.set(key, folded.length); folded.push(row); }
    else folded[previous] = { ...row, id: folded[previous].id };
  }
  return folded;
}
function isAttention(status: RowStatus) {
  return ["failed", "blocked", "outcome_unknown", "unknown", "pending"].includes(status);
}

function ExecutionLine({ row, busy, isZh, compact = false, toolLabel = false }: { row: ExecutionRow; busy: boolean; isZh: boolean; compact?: boolean; toolLabel?: boolean }) {
  const state = visibleStatus(row, busy);
  return <li className={`execution-line ${state}`}>
    <span className={`execution-mark ${state}`} aria-hidden="true">{state === "running" ? "◌" : state === "completed" ? "✓" : state === "failed" ? "×" : isAttention(state) ? "!" : "·"}</span>
    <span className="execution-line-text">
      <span className="execution-line-summary">{toolLabel && ["completed", "running"].includes(state) && toolLabels[row.name] ? toolLabels[row.name][isZh ? 0 : 1] : row.summary}</span>
      {row.details.child_id != null && <small>{isZh ? `${String(row.details.label || row.details.domain)}子智能体 · 只读建议` : `${String(row.details.domain)} specialist · read-only advice`}</small>}
      <span className="execution-line-state">{statuses[state][isZh ? 0 : 1]}</span>
      {!compact && <small>
        <span>{isZh ? sources[row.source || ""] || row.source : row.source}</span>
        {row.latencyMs != null && ` · ${row.latencyMs} ms`}
        {row.recordedAt && <time dateTime={row.recordedAt}> · {new Date(row.recordedAt).toLocaleTimeString(isZh ? "zh-CN" : "en-US", { hour12: false })}</time>}
      </small>}
      {!compact && Object.keys(row.details).length > 0 && <details className="execution-parameters">
        <summary>{isZh ? "参数与依据" : "Parameters and evidence"}</summary>
        <pre>{JSON.stringify(row.details, null, 2)}</pre>
      </details>}
    </span>
  </li>;
}

/** A compact work disclosure shared by streamed chat and persisted business journals. */
export function ExecutionTimeline({ events, trace = [], busy = false, status = "", children }: {
  events?: ExecutionEvent[]; trace?: AgentTraceItem[]; busy?: boolean; status?: string; children?: React.ReactNode;
}) {
  const { isZh } = useLanguage();
  const rows: ExecutionRow[] = [
    ...(events || []).map((event, index) => ({
      id: `event-${index}`, name: event.name, summary: event.summary,
      status: event.status, source: event.source, recordedAt: event.recorded_at, details: event.details || {},
    })),
    ...traceToExecutionRows(trace),
  ];
  if (!rows.length && !busy) return null;
  const currentRows = foldSubagentRows(rows);
  const attention = currentRows.filter((row, index) => {
    if (!isAttention(visibleStatus(row, busy))) return false;
    if (row.name !== "approval.wait" || row.details.approval_id == null) return true;
    return !currentRows.slice(index + 1).some(later => later.name === "approval.decision" && later.status === "completed" && later.details.approval_id === row.details.approval_id);
  });
  // TaskStep is the business-level projection. Keep internal lifecycle/audit rows in a second disclosure.
  const hasBusinessSteps = rows.some(row => row.details.step_id != null);
  const candidateRows = hasBusinessSteps ? rows.filter(row => row.details.step_id != null || row.recordedAt != null || isAttention(visibleStatus(row, busy))) : rows;
  const keyRows = foldSubagentRows(candidateRows);
  const keyIds = new Set(keyRows.map(row => row.id));
  const auditRows = rows.filter(row => !keyIds.has(row.id));
  const running = [...currentRows].reverse().find(row => row.status === "running");
  const last = rows[rows.length - 1];
  const focus = busy ? running || last : last;
  const title = busy ? status || focus?.summary || (isZh ? "正在处理请求…" : "Processing request…")
    : focus?.summary || (isZh ? "执行记录" : "Execution record");
  // Historical success is folded. Important non-success states stay outside the disclosure.
  const preview = busy ? currentRows.slice(-2) : foldSubagentRows(attention).filter(row => isAttention(visibleStatus(row, busy))).slice(-2);
  return <section className="execution-compact" aria-label={isZh ? "执行过程" : "Execution process"}>
    <div className={`execution-current ${busy ? "running" : focus ? visibleStatus(focus, false) : ""}`} role="status" aria-live="polite">
      <span className={`execution-mark ${busy ? "running" : ""}`} aria-hidden="true">{busy ? "◌" : "›"}</span>
      <span>{title}</span>
    </div>
    {!!preview.length && <ul className="execution-preview">{preview.map(row => <ExecutionLine key={row.id} row={row} busy={busy} isZh={isZh} compact toolLabel />)}</ul>}
    {!busy && attention.length > 2 && <small className="execution-attention-count">{isZh ? `还有 ${attention.length - 2} 条待核对记录，展开查看` : `${attention.length - 2} more records need review`}</small>}
    {!!rows.length && <details className="execution-timeline">
      <summary>{isZh ? "查看执行记录" : "View execution record"}<span className="execution-record-count"> · {keyRows.length}</span></summary>
      <ol>{keyRows.map(row => <ExecutionLine key={row.id} row={row} busy={busy} isZh={isZh} toolLabel />)}</ol>
      {!!auditRows.length && <details className="execution-audit"><summary>{isZh ? "完整审计记录" : "Full audit record"} · {auditRows.length}</summary>
        <ol>{auditRows.map(row => <ExecutionLine key={row.id} row={row} busy={busy} isZh={isZh} />)}</ol>
      </details>}
      {children}
      <p className="execution-disclaimer">{isZh ? "仅展示执行事件与公开摘要，不是模型内部完整思考；批准不等于执行完成。" : "Execution events and public summaries, not private model reasoning. Approval is not execution."}</p>
    </details>}
  </section>;
}
