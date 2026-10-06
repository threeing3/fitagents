import React, { useCallback, useEffect, useRef, useState } from "react";
import { responsibilityApi } from "./api";
import { useLanguage } from "./LanguageContext";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { ProposalDiff } from "./PlanReview";
import { RecordedTracePanel } from "./RecordedTracePanel";
import type { ApprovalActivity, Responsibility } from "./types";

export type ResponsibilityGateway = Omit<typeof responsibilityApi, "reconcile"> & Partial<Pick<typeof responsibilityApi, "reconcile">>;
type Props = { userId: string; gateway?: ResponsibilityGateway; demoAdvance?: () => void };

const statusZh: Record<string, string> = {
  active: "跟踪中", paused: "已暂停", cancelled: "已取消", expired: "已到期",
  pending: "待审批", approved: "已批准 · 等待后台执行", executing: "执行中",
  executed: "已执行", denied: "已拒绝", stale: "已失效 · 责任或计划变化",
  failed: "执行失败", outcome_unknown: "执行结果未知 · 需人工核对",
};
const reasons: Record<string, string> = {
  insufficient_recovery_evidence: "恢复证据不足", no_reduction_signal: "未达到演示减量条件",
  symptoms_require_manual_review: "存在症状记录，需人工评估", unresolved_adjustment_exists: "仍有未解决的调整",
  missing_or_ambiguous_plan: "没有唯一有效计划", no_future_dated_training: "没有适用的未来训练日期",
  proposal_validation_failed: "草案未通过组数或计划校验", review_evidence_stale: "复盘证据过旧，不提出调整",
  authority_not_granted: "没有对应授权", responsibility_or_plan_changed: "责任或计划已变化",
};
export function activityLabel(item: ApprovalActivity, isZh: boolean): string {
  if (item.status === "approved" && item.job_status === "running") return isZh ? "任务已领取 · 执行结果未确认" : "Job claimed · execution unconfirmed";
  // A completed job can be skipped; never infer a successful plan write from it.
  if (item.status === "executed") {
    const verified = item.job_status === "completed" && item.result.status === "adjusted" && item.result.verified === true;
    return verified ? (isZh ? "已执行 · 计划校验通过" : "Executed · plan verified")
      : (isZh ? "执行确认不完整 · 需核对" : "Execution verification incomplete");
  }
  return isZh ? (statusZh[item.status] || item.status) : item.status;
}

export function ResponsibilityView({ userId, gateway = responsibilityApi, demoAdvance }: Props) {
  const [recovery, setRecovery] = useState<ApprovalActivity | null>(null);
  const { isZh } = useLanguage();
  const [tasks, setTasks] = useState<Responsibility[]>([]);
  const [activities, setActivities] = useState<ApprovalActivity[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [weeks, setWeeks] = useState(4);
  const [weekday, setWeekday] = useState(6);
  const [clock, setClock] = useState("18:00");
  const [confirm, setConfirm] = useState<ApprovalActivity | null>(null);
  const confirmationButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (confirm) confirmationButton.current?.focus(); }, [confirm]);
  const lockKey = `fitagent.responsibility-uncertain.${userId}`;
  const [uncertain, setUncertain] = useState(() => sessionStorage.getItem(lockKey) === "1");
  const [checked, setChecked] = useState(false);
  const alive = useRef(true);
  const writing = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const ticket = ++sequence.current;
    try {
      const [nextTasks, nextActivities] = await Promise.all([gateway.list(), gateway.history()]);
      if (!alive.current || ticket !== sequence.current) return;
      setTasks(nextTasks); setActivities(nextActivities); setError(""); setChecked(true);
    } catch (cause) {
      if (alive.current && ticket === sequence.current) setError(String(cause instanceof Error ? cause.message : cause));
    } finally {
      if (alive.current && ticket === sequence.current) setLoading(false);
    }
  }, [gateway]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      if (!writing.current && document.visibilityState === "visible") void refresh();
    }, 15000);
    return () => { alive.current = false; ++sequence.current; window.clearInterval(timer); };
  }, [refresh]);

  async function mutate(operation: () => Promise<unknown>, message: string) {
    if (writing.current || uncertain) return;
    writing.current = true; ++sequence.current; setBusy(true); setNotice(""); setError("");
    sessionStorage.setItem(lockKey, "1");
    try {
      await operation();
      if (!alive.current) return;
      sessionStorage.removeItem(lockKey);
      setConfirm(null); setNotice(message);
    } catch (cause) {
      // A lost response is not permission to repeat a write. Retain lock across reload.
      sessionStorage.setItem(lockKey, "1");
      if (alive.current) { setUncertain(true); setChecked(false); setError(String(cause instanceof Error ? cause.message : cause)); }
    } finally {
      writing.current = false;
      if (alive.current) { setBusy(false); await refresh(); }
    }
  }
  function time(value: string | null, zone?: string): string {
    if (!value) return isZh ? "无待执行时间" : "No scheduled wake";
    try { return new Intl.DateTimeFormat(isZh ? "zh-CN" : "en-US", { timeZone: zone, dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
    catch { return value; }
  }
  const disabled = busy || uncertain || loading || !!error;
  const pending = activities.filter(item => item.status === "pending" && item.tool_name === "plan.reduce_sets");
  return <div className="responsibility-view">
    <div className="dash-header"><h2>{isZh ? "长期跟踪" : "Long-term follow-up"}</h2>
      <button className="pill-btn" disabled={busy} onClick={() => void refresh()}>{isZh ? "刷新状态" : "Refresh status"}</button></div>
    <p className="responsibility-subtitle">{isZh ? "约定复盘周期，让每一次建议、批准和实际执行都有可追溯的状态。" : "Track review schedules, approvals and verified execution separately."}</p>
    {demoAdvance && <div className="responsibility-banner">合成数据交互演示，不连接业务数据库，后台执行为界面模拟。
      <button className="pill-btn" disabled={busy} onClick={() => { demoAdvance(); void refresh(); }}>推进演示后台</button></div>}
    {loading && <p role="status">{isZh ? "正在读取状态…" : "Loading…"}</p>}
    {error && <p role="alert" className="responsibility-error">{isZh ? "状态读取或操作未确认：" : "Read or action unconfirmed: "}{error}</p>}
    {notice && <p role="status" className="responsibility-banner">{notice}</p>}
    {uncertain && <div className="responsibility-banner" role="alert">
      {isZh ? "上次操作结果未确认。不会自动重发，请刷新核对记录后再继续。" : "The previous write is unconfirmed. It will not be retried automatically. Refresh and inspect records."}
      <button className="pill-btn" disabled={!checked || busy} onClick={() => { sessionStorage.removeItem(lockKey); setUncertain(false); }}>{isZh ? "已核对，解除操作锁" : "Inspected records · unlock"}</button>
    </div>}
    <section className="responsibility-pending"><h3>{isZh ? `待审批草案 (${pending.length})` : `Pending proposals (${pending.length})`}</h3>
      {!loading && !error && !pending.length && <p>{isZh ? "没有待审批草案；证据不足时不会强行提出调整。" : "No pending proposals."}</p>}
      {pending.map(item => <article className="dash-card responsibility-proposal" key={item.approval_id}>
        <strong>{item.tool_description}</strong><p>{item.input_preview.day_date} · {isZh ? `各动作减 ${item.input_preview.reduce_by ?? "?"} 组` : `Reduce ${item.input_preview.reduce_by ?? "?"} sets per exercise`}</p>
        <ProposalDiff item={item} />
        <p>{item.input_preview.reason}</p><p>{isZh ? "有效期：" : "Expires: "}{time(item.expires_at)}</p>
        <ExecutionTimeline events={item.context.execution_events} />
        <p>{isZh ? "其他日期、动作类型和饮食信息保持不变。" : "Other dates, exercise types and nutrition remain unchanged."}</p>
        <small>{item.approval_id}</small>
        <details><summary>{isZh ? "查看提案依据" : "Evidence"}</summary><p>{isZh ? "疲劳评分均值：" : "Mean fatigue: "}{item.context.review_signal?.average_fatigue ?? "—"}</p>
          {(item.context.review_signal?.evidence || []).map(source => <p key={source.id}>{source.table}:{source.id}</p>)}</details>
        <div className="responsibility-actions"><button className="pill-btn" disabled={disabled || item.tool_name !== "plan.reduce_sets" || Date.parse(item.expires_at) <= Date.now()} onClick={() => setConfirm(item)}>{isZh ? "审阅并批准" : "Review and approve"}</button>
          <button className="pill-btn" disabled={disabled} onClick={() => void mutate(() => gateway.decide(item.approval_id, "deny"), isZh ? "已拒绝，计划不变。" : "Denied; plan unchanged.")}>{isZh ? "拒绝" : "Deny"}</button></div>
      </article>)}
    </section>
    {confirm && <section className="dash-card responsibility-confirm" role="dialog" aria-modal="false" aria-label={isZh ? "确认单次调整" : "Confirm one adjustment"}>
      <h3>{isZh ? "确认单次调整" : "Confirm one adjustment"}</h3><p>{confirm.input_preview.day_date} · {isZh ? `各动作减${confirm.input_preview.reduce_by}组` : `Reduce ${confirm.input_preview.reduce_by} sets`}</p>
      <p>{isZh ? "只批准这一个草案，不授予未来自动修改权限。批准后仍需后台校验并执行。" : "Authorize this proposal only. A worker must validate and execute it."}</p>
      <button ref={confirmationButton} className="pill-btn" disabled={disabled} onClick={() => void mutate(() => gateway.decide(confirm.approval_id, "approve"), isZh ? "已批准，不代表已执行；执行结果见下方历史。" : "Approved, not necessarily executed; see execution history below.")}>{isZh ? "确认批准一次" : "Approve once"}</button>
      <button className="pill-btn" disabled={busy} onClick={() => setConfirm(null)}>{isZh ? "返回" : "Back"}</button>
    </section>}
    <section className="responsibility-tracking"><h3>{isZh ? "责任与最近复盘" : "Responsibilities and latest review"}</h3>
      {!loading && !error && !tasks.length && <p>{isZh ? "暂无长期责任，可以先创建一个四周复盘。" : "No responsibilities yet."}</p>}
      <div className="responsibility-grid">{tasks.map(task => <article className="dash-card" key={task.id}>
        <div className="responsibility-card-heading"><strong>{isZh ? "每周训练复盘" : "Weekly training review"}</strong><span className="responsibility-status">{isZh ? statusZh[task.status] || task.status : task.status}</span></div>
        <p>{task.configuration.source_instruction}</p><p>{task.configuration.timezone}</p>
        <p>{isZh ? "下一次：" : "Next: "}{time(task.configuration.next_wake_at, task.configuration.timezone)}</p>
        <p>{isZh ? "责任到期：" : "Ends: "}{time(task.configuration.ends_at, task.configuration.timezone)}</p>
        <p>{isZh ? "已完成复盘：" : "Reviews: "}{task.progress.reviews_completed || 0}</p>
        <ExecutionTimeline events={task.progress.last_review?.execution_events} />
        {task.progress.last_review && <details><summary>{isZh ? "查看最近复盘与证据" : "Latest review and evidence"}</summary>
          <p>{task.progress.last_review.week_start} — {task.progress.last_review.week_end}</p>
          {(task.progress.last_review.memories || []).map(memory => <div key={memory.id}><p>{memory.summary}</p><small>{isZh ? "来源记录：" : "Evidence: "}{(memory.evidence || []).map(source => `${source.table}:${source.id}`).join(" · ")}</small></div>)}
          {task.progress.last_review.adjustment_proposal?.reason && <p>{isZh ? reasons[task.progress.last_review.adjustment_proposal.reason] || task.progress.last_review.adjustment_proposal.reason : task.progress.last_review.adjustment_proposal.reason}</p>}
          {task.progress.last_review.adjustment_proposal?.approval_id && <p>{isZh ? "草案编号：" : "Approval: "}{task.progress.last_review.adjustment_proposal.approval_id}</p>}
        </details>}
        <small>{task.id}</small><div className="responsibility-actions">
          {task.status === "active" && <button className="pill-btn" disabled={disabled} onClick={() => void mutate(() => gateway.lifecycle(task.id, "pause"), isZh ? "责任已暂停。" : "Paused.")}>{isZh ? "暂停" : "Pause"}</button>}
          {task.status === "paused" && <button className="pill-btn" disabled={disabled} onClick={() => void mutate(() => gateway.lifecycle(task.id, "resume"), isZh ? "责任已恢复，旧任务不会重新执行。" : "Resumed with a fresh schedule.")}>{isZh ? "恢复" : "Resume"}</button>}
          {["active", "paused"].includes(task.status) && <button className="pill-btn" disabled={disabled} onClick={() => { if (window.confirm(isZh ? "取消此责任？已完成复盘会保留，责任不能恢复。" : "Cancel permanently? Existing reviews are retained.")) void mutate(() => gateway.lifecycle(task.id, "cancel"), isZh ? "责任已取消，历史保留。" : "Cancelled; history retained."); }}>{isZh ? "取消责任" : "Cancel"}</button>}
        </div>
      </article>)}</div>
    </section>
    <section className="responsibility-history-section"><h3>{isZh ? "审批与执行历史" : "Approval and execution history"}</h3>
      {!loading && !error && !activities.length && <p>{isZh ? "暂无动作历史。" : "No history yet."}</p>}
      {activities.map(item => <article className="responsibility-history" key={item.approval_id}><strong>{activityLabel(item, isZh)}</strong>
        <p>{item.input_preview.day_date || item.tool_description} · {time(item.created_at)}</p><small>{item.approval_id}</small>
        {item.result.reason && <p>{isZh ? reasons[item.result.reason] || item.result.reason : item.result.reason}</p>}
        {item.error && <p className="responsibility-error">{item.error}</p>}
        {gateway.reconcile && item.job_status === "running" && item.job_id && item.job_attempts && <button className="pill-btn" disabled={disabled} onClick={() => setRecovery(item)}>{isZh ? "核对中断执行" : "Reconcile interrupted execution"}</button>}
        <ExecutionTimeline events={item.context.execution_events} />
        {item.execution_trace_run_id && <RecordedTracePanel
          key={`${userId}:${item.execution_trace_run_id}`}
          ownerId={userId} runId={item.execution_trace_run_id}
        />}
      </article>)}
    </section>
    <section className="dash-card responsibility-create"><h3>{isZh ? "创建训练复盘" : "Create weekly review"}</h3>
      <form onSubmit={event => {
        event.preventDefault();
        const [hour, minute] = clock.split(":").map(Number);
        if (!Number.isInteger(weeks) || weeks < 1 || weeks > 52 || !Number.isInteger(hour) || !Number.isInteger(minute)) return;
        void mutate(() => gateway.create({ weeks, weekday, hour, minute, source_instruction: `创建${weeks}周每周${"一二三四五六日"[weekday]}${clock}训练复盘，调整计划先问我` }), isZh ? "责任已创建；按账户时区执行复盘。" : "Review created in your account timezone.");
      }}>
        <label>{isZh ? "持续周数" : "Weeks"}<input type="number" min={1} max={52} required value={weeks} disabled={disabled} onChange={event => setWeeks(Number(event.target.value))} /></label>
        <label>{isZh ? "复盘日" : "Review day"}<select value={weekday} disabled={disabled} onChange={event => setWeekday(Number(event.target.value))}>{["一", "二", "三", "四", "五", "六", "日"].map((day, index) => <option key={day} value={index}>{isZh ? `每周${day}` : ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][index]}</option>)}</select></label>
        <label>{isZh ? "账户当地时间" : "Account local time"}<input type="time" required value={clock} disabled={disabled} onChange={event => setClock(event.target.value)} /></label>
        <button className="pill-btn" disabled={disabled}>{isZh ? "创建责任" : "Create review"}</button>
      </form><p>{isZh ? "复盘可生成待审批草案；未批准不改计划。减量条件目前是演示规则，不是临床结论。" : "Reviews may draft changes; plans never change without approval. Reduction thresholds are demo rules, not clinical conclusions."}</p>
    </section>
    {recovery && <section className="dash-card responsibility-confirm" role="dialog" aria-label={isZh ? "核对中断执行" : "Reconcile interrupted execution"}>
      <h3>{isZh ? "核对中断执行" : "Reconcile interrupted execution"}</h3>
      <p>{isZh ? "这不是重新执行。仍在持有执行锁的任务会被拒绝核对；无法确认副作用时保留结果未知，需要人工检查。" : "This never retries. Active execution locks block reconciliation; uncertain effects remain unknown for manual inspection."}</p>
      <button className="pill-btn" disabled={disabled} onClick={() => {
        if (!recovery.job_id || !recovery.job_attempts || !gateway.reconcile) return;
        void mutate(() => gateway.reconcile!(recovery.job_id!, recovery.job_attempts!), isZh ? "核对结果已保存；不会重新入队，请查看历史状态。" : "Reconciliation saved without requeue; inspect history.");
        setRecovery(null);
      }}>{isZh ? "确认核对，不重跑" : "Reconcile, never retry"}</button>
      <button className="pill-btn" onClick={() => setRecovery(null)}>{isZh ? "返回" : "Back"}</button>
    </section>}
  </div>;
}
