import { useEffect, useState } from "react";
import { api } from "./api";
import { useLanguage } from "./LanguageContext";
import type { RecordedTrace } from "./types";
import { StreamEventsPanel } from "./StreamEventsPanel";

export function RecordedTracePanel({ runId, ownerId }: { runId: string; ownerId?: string }) {
  const { isZh } = useLanguage();
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [trace, setTrace] = useState<RecordedTrace | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setTrace(null); setError(false); setOpen(false);
  }, [runId, ownerId]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setError(false);
    api<RecordedTrace>(`/v1/agent-runs/${encodeURIComponent(runId)}/trace`)
      .then(value => { if (active) setTrace(value); })
      .catch(() => { if (active) { setError(true); setTrace(null); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, runId, attempt, ownerId]);
  return <details className="recorded-trace" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>{isZh ? "完整执行追踪" : "Recorded execution trace"}</summary>
    <small>{isZh ? "只读历史 · 不重新执行工具 · 不展示隐藏推理" : "Read-only history · no tool execution · no private reasoning"}</small>
    {loading && <p role="status">{isZh ? "正在读取已保存的轨迹…" : "Loading recorded trace…"}</p>}
    {error && <p role="alert">{isZh ? "轨迹暂不可读取，或你无权访问。" : "Trace unavailable or access denied."}</p>}
    {open && <button type="button" disabled={loading} onClick={() => setAttempt(n => n + 1)}>{isZh ? "刷新记录" : "Refresh"}</button>}
    {trace && <>
      <p>{isZh ? "运行" : "Run"}：{trace.run_id} · {trace.status} · {trace.events.length} {isZh ? "条记录" : "records"}</p>
      {trace.write_receipts?.map((receipt, index) => <p key={`${receipt.kind}:${index}`}>
        {receipt.state === "committed" && receipt.verification === "independent_committed_read"
          ? (isZh ? `独立核对：${receipt.kind === "plan_adjustment" ? "计划调整" : "训练记录"}已提交；这不代表整个请求已完成。` : "Verified business commit; the overall request may still be incomplete.")
          : (receipt.reason === "approval_not_executed"
            ? (isZh ? "审批尚未执行；用户批准不等于计划已更新。" : "Approval not executed; consent does not prove a plan update.")
            : (isZh ? "业务写入尚不可确认；不能据此判断没有写入，请勿重复操作。" : "Business write unconfirmed; this does not prove absence. Do not repeat the operation."))}
        {receipt.record_id && <small> · {receipt.record_id}</small>}
      </p>)}
      <p className="trace-coverage">{isZh ? "展示已保存的步骤、工具和检查点；未记录或截断的输入不可恢复，不能精确复现每次模型调用。" : "Recorded steps, tools and checkpoints; missing or truncated inputs cannot reproduce exact model requests."}</p>
      {trace.coverage.damaged_tail && <p role="alert">{isZh ? "记录尾部缺损，仅展示完整前缀；不能确认整个执行结果。" : "Damaged journal tail: only the complete prefix is shown; overall execution is unconfirmed."}</p>}
      {trace.coverage.liveness === "not_checked" && <p>{isZh ? "未核对进程是否仍在运行；记录中断不等于工具未执行，不会自动重跑。" : "Process liveness was not checked; interruption does not prove a tool did not execute. No automatic retry."}</p>}
      {!trace.events.length && <p>{isZh ? "本次运行没有已保存的执行事件。" : "No recorded events."}</p>}
      <ol>{trace.events.map(event => <li key={event.event_id} className={event.child_id ? "trace-child" : ""}>
        <details><summary>{event.order}. {event.name} · {event.status} · {event.latency_ms} ms</summary>
          <p>{event.summary}</p>
          <small>{event.recorded_at || (isZh ? "时间未记录" : "Time unavailable")} · {event.source}</small>
          <dl><dt>{isZh ? "事件编号" : "Event"}</dt><dd>{event.event_id}</dd>
            <dt>{isZh ? "父任务" : "Parent"}</dt><dd>{event.parent_id}</dd>
            {event.child_id && <><dt>{isZh ? "子任务" : "Child"}</dt><dd>{event.child_id}</dd></>}
            {event.step_id && <><dt>{isZh ? "业务步骤" : "Step"}</dt><dd>{event.step_id}</dd></>}
          </dl>
          <h4>{isZh ? "已记录输入" : "Recorded input"}</h4><pre>{JSON.stringify(event.input, null, 2)}</pre>
          <h4>{isZh ? "已记录结果与依据" : "Recorded output and evidence"}</h4><pre>{JSON.stringify(event.output, null, 2)}</pre>
          {!!event.output && typeof event.output === "object" && "write_receipt" in event.output && <p>{isZh ? "写入凭据：只有独立读取确认已提交，才表示业务写入已确认；工具成功不等于提交成功。" : "Write receipt: only an independent committed read confirms the business write; tool success is not commit confirmation."}</p>}
          {!!event.error && <pre>{JSON.stringify(event.error, null, 2)}</pre>}
        </details>
      </li>)}</ol>
      <details><summary>{isZh ? "上下文与工具计划快照" : "Context and tool-plan snapshot"}</summary><pre>{JSON.stringify(trace.snapshot, null, 2)}</pre></details>
      <StreamEventsPanel key={`${ownerId || "anonymous"}:${runId}`} runId={runId} ownerId={ownerId} />
    </>}
  </details>;
}
