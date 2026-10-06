import React, { useEffect, useRef, useState } from "react";
import { fetchSubagentCatalogs, reconcileSubagentCatalog, stopSubagentTree, type SubagentCatalog } from "./api";
import { useLanguage } from "./LanguageContext";
import { SubagentTurnForm } from "./SubagentTurnForm";
import { useAuth } from "./AuthContext";
import { listChildReceiptParents, readChildReceipt } from "./subagentRequestReceipt";

const roles: Record<string, [string, string]> = {
  evidence_analysis: ["证据分析", "Evidence analysis"], training: ["训练", "Training"],
  nutrition: ["饮食", "Nutrition"], recovery: ["恢复", "Recovery"],
  plan_planning: ["方案规划", "Candidate planning"], single_review: ["复盘", "Review"],
};
const statuses: Record<string, [string, string]> = {
  pending: ["已记录等待态，存活未核对", "Recorded pending; liveness unverified"],
  running: ["已记录运行态，存活未核对", "Recorded running; liveness unverified"],
  completed: ["已返回建议，未执行变更", "Advice returned; no changes executed"],
  failed: ["未完成", "Not completed"], skipped: ["已跳过", "Skipped"],
};

export function SubagentCatalogPanel({ sessionId, busy }: { sessionId: string; busy: boolean }) {
  const { isZh } = useLanguage();
  const { user } = useAuth();
  const [rows, setRows] = useState<SubagentCatalog[]>([]);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [recoveryError, setRecoveryError] = useState(false);
  const [stopParent, setStopParent] = useState<string | null>(null);
  const [stopNote, setStopNote] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const scope = `${user?.user_id}:${sessionId}`;
  const [inspected, setInspected] = useState<{ scope: string; parents: string[] }>({ scope: "", parents: [] });
  const currentSession = useRef(sessionId);
  const loadedScope = useRef("");
  const missingParents = [...new Set([...listChildReceiptParents(user?.user_id, sessionId), ...(inspected.scope === scope ? inspected.parents : [])])].filter(parent => !rows.some(row => row.parent_id === parent));
  currentSession.current = sessionId;
  useEffect(() => { setConfirm(null); setRecovering(false); setRecoveryError(false); setStopParent(null); setStopNote(null); setStopping(false); }, [sessionId]);
  async function stopTree(row: SubagentCatalog) {
    const requestedSession = sessionId;
    setStopping(true);
    try {
      const result = await stopSubagentTree(sessionId, row);
      if (currentSession.current === requestedSession) {
        setStopParent(null);
        setStopNote(result.status === "cancel_requested" ? (isZh ? "协作停止请求已接收；请刷新目录确认最终状态。" : "Tree stop requested; refresh the catalog for final state.") : (isZh ? "协作控制已有终态，请刷新目录。" : "Tree control is terminal; refresh its catalog."));
        setRefresh(value => value + 1);
      }
    } catch { if (currentSession.current === requestedSession) setStopNote(isZh ? "停止指令未确认；可能目录版本已变化，请刷新。" : "Stop unconfirmed; revision may have changed. Refresh."); }
    finally { if (currentSession.current === requestedSession) setStopping(false); }
  }
  const reconcile = async (row: SubagentCatalog) => {
    const requestedSession = sessionId;
    setRecovering(true); setRecoveryError(false);
    try {
      await reconcileSubagentCatalog(requestedSession, row);
      if (currentSession.current === requestedSession) { setConfirm(null); setRefresh(value => value + 1); }
    } catch {
      if (currentSession.current === requestedSession) setRecoveryError(true);
    } finally {
      if (currentSession.current === requestedSession) setRecovering(false);
    }
  };
  useEffect(() => {
    const controller = new AbortController();
    if (loadedScope.current !== scope) { setRows([]); loadedScope.current = scope; }
    setError(false); setLoading(true);
    fetchSubagentCatalogs(sessionId, controller.signal).then(value => {
      if (!controller.signal.aborted) setRows(value);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [sessionId, busy, refresh, scope]);
  return <details className="subagent-catalog" aria-label={isZh ? "会话子任务目录" : "Session child task catalog"}>
    <summary>{isZh ? "子任务记录" : "Child task records"}{rows.length > 0 ? ` · ${rows.length}` : ""}</summary>
    <p>{isZh ? "会话级独立记录，不代表某条回复的思考。等待或运行记录不证明进程仍存活。" : "Session-level records, not hidden reasoning. Pending/running records do not prove process liveness."}</p>
    <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading}>{isZh ? "刷新记录" : "Refresh records"}</button>
    <details><summary>{isZh ? "独立领域咨询" : "Domain consultation"}</summary>
      <SubagentTurnForm key={`${user?.user_id}:${sessionId}`} sessionId={sessionId} busy={busy || recovering} onRecorded={() => setRefresh(value => value + 1)} />
    </details>
    {loading && <p role="status">{isZh ? "正在读取记录…" : "Loading records…"}</p>}
    {error && <p role="alert">{isZh ? "读取失败，未确认任务状态；请重试。" : "Could not load records; task status unconfirmed. Retry."}</p>}
    {recoveryError && <p role="alert">{isZh ? "核对未完成：任务可能仍在运行或记录版本已变化，请刷新。未重新运行任务。" : "Reconciliation incomplete: execution may be active or revision changed. Refresh; no task was retried."}</p>}
    {stopNote && <p role="status">{stopNote}</p>}
    {missingParents.length > 0 && <details><summary>{isZh ? "目录外待确认请求" : "Unconfirmed requests outside recent catalog"} · {missingParents.length}</summary>
      <p>{isZh ? "原任务不在当前目录中；这里只查询原回执，不重跑或续话。" : "The original task is not in this catalog. Inspect its receipt only; no retry or continuation."}</p>
      {missingParents.map(parent => <SubagentTurnForm key={`${user?.user_id}:${sessionId}:${parent}`} sessionId={sessionId} busy={busy} receiptParentId={parent} onRecorded={() => setInspected(previous => ({ scope, parents: [...new Set([...(previous.scope === scope ? previous.parents : []), parent])] }))} />)}
    </details>}
    {!loading && !error && rows.length === 0 && <p>{isZh ? "暂无持久化子任务记录；不等于任务成功或失败。" : "No persisted records; this does not imply success or failure."}</p>}
    {rows.map(row => <section key={row.parent_id}>
      <small>{isZh ? "目录版本" : "Catalog revision"} {row.revision} · {row.recorded_at}</small>
      <ul>{row.children.map(child => <li key={child.child_id}>
        {roles[child.domain]?.[isZh ? 0 : 1] ?? (isZh ? "子任务" : "Child task")}：
        {statuses[child.status]?.[isZh ? 0 : 1] ?? (isZh ? "状态未确认" : "Status unconfirmed")}
        {child.failure_reason === "parent_cancelled" && (isZh ? "（父任务取消）" : " (parent cancelled)")}
        {child.failure_reason === "consumer_closed" && (isZh ? "（执行链关闭）" : " (execution chain closed)")}
        {child.failure_reason === "evidence_changed" && (isZh ? "（证据已变化）" : " (evidence changed)")}
        {child.failure_reason === "execution_interrupted" && (isZh ? "（已核对执行中断）" : " (interruption reconciled)")}
      </li>)}</ul>
      {row.stop_control?.protocol === 1 && row.children.some(child => ["pending", "running"].includes(child.status)) && (
        stopParent === row.parent_id ? <div>
          <p>{isZh ? "停止当前协作树，后续领域与规划不再继续；已产生费用无法撤回。" : "Stop the current tree and downstream analysis/planning; incurred charges cannot be undone."}</p>
          <button type="button" disabled={stopping || loading} onClick={() => void stopTree(row)}>{isZh ? "确认停止协作，不重跑" : "Confirm tree stop, no retry"}</button>
          <button type="button" disabled={stopping} onClick={() => setStopParent(null)}>{isZh ? "返回协作" : "Back"}</button>
        </div> : <button type="button" disabled={stopping || loading} onClick={() => setStopParent(row.parent_id)}>{isZh ? "停止协作" : "Stop collaboration"}</button>
      )}
      {(row.continuation_available || readChildReceipt(user?.user_id, sessionId, row.parent_id) || (inspected.scope === scope && inspected.parents.includes(row.parent_id))) && <details><summary>{isZh ? "补充咨询" : "Follow-up consultation"}</summary>
        <SubagentTurnForm key={`${user?.user_id}:${sessionId}:${row.parent_id}`} sessionId={sessionId} busy={busy || recovering || loading} row={row} onRecorded={() => { setInspected(previous => ({ scope, parents: [...new Set([...(previous.scope === scope ? previous.parents : []), row.parent_id])] })); setRefresh(value => value + 1); }} />
      </details>}
      {row.execution_lease?.protocol === 1 && row.children.some(child => ["pending", "running"].includes(child.status)) && (
        confirm === row.parent_id ? <div>
          <p>{isZh ? "仅在服务器确认执行锁已释放后，关闭遗留状态；不会重新运行模型、工具或修改计划。" : "Close stale states only after the server verifies the execution lock is released. No model/tool retry or plan changes."}</p>
          <button type="button" disabled={recovering || busy} onClick={() => void reconcile(row)}>{isZh ? "确认核对，不重跑" : "Confirm reconciliation, no retry"}</button>
          <button type="button" disabled={recovering} onClick={() => setConfirm(null)}>{isZh ? "取消核对" : "Cancel"}</button>
        </div> : <button type="button" disabled={busy || recovering} onClick={() => setConfirm(row.parent_id)}>{isZh ? "核对遗留状态" : "Reconcile stale state"}</button>
      )}
    </section>)}
  </details>;
}
