import React, { useRef, useState } from "react";
import { cancelSubagentRequest, fetchSubagentRequestStatus, reconcileSubagentRequest, requestSubagentTurn, type SubagentCatalog, type SubagentTurnResult } from "./api";
import { useLanguage } from "./LanguageContext";
import { useAuth } from "./AuthContext";
import { clearChildReceipt, readChildReceipt, saveChildReceipt } from "./subagentRequestReceipt";

export function SubagentTurnForm({ sessionId, busy, row, receiptParentId, onRecorded }: {
  sessionId: string; busy: boolean; row?: SubagentCatalog; receiptParentId?: string; onRecorded: () => void;
}) {
  const { isZh } = useLanguage();
  const { user } = useAuth();
  const ownerId = user?.user_id;
  const parentId = row?.parent_id ?? receiptParentId;
  const recoveryOnly = receiptParentId !== undefined;
  const [message, setMessage] = useState("");
  const [role, setRole] = useState("training");
  const [pending, setPending] = useState(false);
  const [key, setKey] = useState<string | null>(() => readChildReceipt(ownerId, sessionId, parentId));
  const [unknown, setUnknown] = useState(() => !!readChildReceipt(ownerId, sessionId, parentId));
  const [storageError, setStorageError] = useState(false);
  const [result, setResult] = useState<SubagentTurnResult | null>(null);
  const active = useRef(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelNote, setCancelNote] = useState<string | null>(null);
  const [confirmReceipt, setConfirmReceipt] = useState(false);
  const canReconcile = unknown && key && row?.execution_lease?.protocol === 1 && row.children.length > 0 && row.children.every(child => ["failed", "skipped"].includes(child.status));
  async function reconcileReceipt() {
    if (active.current || !canReconcile || !key || !ownerId || !row) return;
    active.current = true; setPending(true);
    try {
      const value = await reconcileSubagentRequest(sessionId, key, row.revision);
      if (value.status === "recorded" && value.result) {
        clearChildReceipt(ownerId, sessionId, parentId);
        setResult(value.result); setUnknown(false); setKey(null); setConfirmReceipt(false); onRecorded();
      }
    } catch { setCancelNote(isZh ? "回执核对未完成；可能仍在运行或目录版本已变化。未重跑任务。" : "Receipt reconciliation incomplete; execution or revision may have changed. No retry."); }
    finally { active.current = false; setPending(false); }
  }
  async function cancel() {
    if (!key || cancelling || !ownerId) return;
    setCancelling(true);
    try {
      const value = await cancelSubagentRequest(sessionId, key);
      setCancelNote(value.status === "cancel_requested"
        ? (isZh ? "已收到停止请求；是否已停止，以最终回执为准。" : "Stop requested; consult the final receipt for confirmation.")
        : (isZh ? "请求已有最终回执，请查询。" : "A final receipt exists; inspect it."));
      setConfirmCancel(false);
    } catch { setCancelNote(isZh ? "停止请求未确认；不要假定任务已停止，请查询原回执。" : "Stop request unconfirmed; do not assume execution stopped. Inspect the receipt."); }
    finally { setCancelling(false); }
  }
  async function submit() {
    if (recoveryOnly || active.current || busy || key || !message.trim() || !ownerId) return;
    active.current = true;
    const requestKey = crypto.randomUUID();
    try { saveChildReceipt(ownerId, sessionId, requestKey, parentId); }
    catch { setStorageError(true); active.current = false; return; }
    setStorageError(false);
    setKey(requestKey); setPending(true); setUnknown(false);
    try {
      const value = await requestSubagentTurn(sessionId, requestKey, message.trim(), role, row);
      clearChildReceipt(ownerId, sessionId, parentId);
      setResult(value); setKey(null); onRecorded();
    } catch { setUnknown(true); }
    finally { active.current = false; setPending(false); }
  }
  async function inspect() {
    if (active.current || !key || !ownerId) return;
    active.current = true; setPending(true);
    try {
      const value = await fetchSubagentRequestStatus(sessionId, key);
      if (value.status === "recorded" && value.result) {
        clearChildReceipt(ownerId, sessionId, parentId);
        setResult(value.result); setUnknown(false); setKey(null); onRecorded();
      } else { setUnknown(true); }
    } catch { setUnknown(true); }
    finally { active.current = false; setPending(false); }
  }
  return <div className="subagent-turn-form">
    <p>{isZh ? "只读咨询：可读取当前记忆与记录，只返回建议，不修改计划。" : "Read-only consultation: reads current memory and records; advice only, no plan changes."}</p>
    {!recoveryOnly && !row && <select aria-label={isZh ? "咨询领域" : "Consultation domain"} value={role} disabled={pending || !!key} onChange={event => setRole(event.target.value)}>
      {[["training", "训练", "Training"], ["nutrition", "饮食", "Nutrition"], ["recovery", "恢复", "Recovery"]].map(([id, zh, en]) => <option key={id} value={id}>{isZh ? zh : en}</option>)}
    </select>}
    {!recoveryOnly && <><textarea aria-label={isZh ? "子任务问题" : "Child task question"} maxLength={4000} value={message} disabled={pending || !!key} onChange={event => setMessage(event.target.value)} />
      <button type="button" disabled={!ownerId || busy || pending || !!key || !message.trim() || (!!row && !row.continuation_available)} onClick={() => void submit()}>{isZh ? (row ? "继续只读咨询" : "启动只读咨询") : (row ? "Continue consultation" : "Start consultation")}</button></>}
    {storageError && <p role="alert">{isZh ? "无法保存请求号，尚未发送咨询。请检查浏览器存储权限。" : "Could not save the receipt key. No request sent; check browser storage permissions."}</p>}
    {pending && <p role="status">{isZh ? "正在处理…" : "Processing…"}</p>}
    {key && (confirmCancel ? <div>
      <p>{isZh ? "请求停止本次执行，不会重跑；已产生的模型费用无法撤回。" : "Request execution stop, never retry; incurred model charges cannot be undone."}</p>
      <button type="button" disabled={cancelling} onClick={() => void cancel()}>{isZh ? "确认停止，不重跑" : "Confirm stop, no retry"}</button>
      <button type="button" disabled={cancelling} onClick={() => setConfirmCancel(false)}>{isZh ? "返回" : "Back"}</button>
    </div> : <button type="button" disabled={cancelling} onClick={() => setConfirmCancel(true)}>{isZh ? "请求停止" : "Request stop"}</button>)}
    {cancelNote && <p role="status">{cancelNote}</p>}
    {canReconcile && (confirmReceipt ? <div>
      <p>{isZh ? "服务器确认执行锁释放后，仅关闭失败任务的孤儿回执，不恢复建议或重跑。" : "Close only the failed task's orphan receipt after the server verifies its lock is released; no advice recovery or retry."}</p>
      <button type="button" disabled={pending} onClick={() => void reconcileReceipt()}>{isZh ? "确认关闭孤儿回执" : "Confirm orphan receipt closure"}</button>
      <button type="button" disabled={pending} onClick={() => setConfirmReceipt(false)}>{isZh ? "返回查询" : "Back to inspection"}</button>
    </div> : <button type="button" disabled={pending} onClick={() => setConfirmReceipt(true)}>{isZh ? "核对请求回执" : "Reconcile request receipt"}</button>)}
    {unknown && <div role="alert"><p>{isZh ? "请求结果未确认。不会自动重跑；请查询原请求回执。" : "Request outcome unconfirmed. No automatic retry; inspect the original receipt."}</p>
      <button type="button" disabled={pending} onClick={() => void inspect()}>{isZh ? "查询原请求" : "Inspect original request"}</button>
    </div>}
    {result && <div role="status"><p>{result.failure_reason === "user_cancelled" ? (isZh ? "本次咨询已停止，未执行变更" : "Consultation stopped; no changes executed") : result.status === "completed" ? (isZh ? "建议已返回，未执行变更" : "Advice returned; no changes executed") : (isZh ? "未完成咨询或已被安全规则拦截" : "Consultation incomplete or blocked by safety rules")}</p>
      <p>{result.advice?.summary}</p>
      {result.advice?.recommendations?.map((text, index) => <p key={index}>{text}</p>)}
      {result.advice?.uncertainties?.map((text, index) => <small key={index}>{text}</small>)}
    </div>}
  </div>;
}
