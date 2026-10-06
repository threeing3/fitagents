import React, { useEffect, useRef, useState } from "react";
import { Bot, ArrowUp, Info, Clock, Zap, Brain, Target, Plus, X } from "lucide-react";
import { fetchAgentRun } from "./api";
import type { SessionState, ChatMessage, AgentTraceItem, AgentRunDetail, Dashboard, ViewName } from "./types";
import { useLanguage } from "./LanguageContext";
import { ExecutionTimeline } from "./ExecutionTimeline";
import { FollowupPanel } from "./FollowupPanel";
import { PendingProposalPreview } from "./PendingProposalPreview";
import { SubagentCatalogPanel } from "./SubagentCatalogPanel";
import { RecordedTracePanel } from "./RecordedTracePanel";

const SUGGESTIONS = [
  { icon: <Target size={14} />, text: "Generate my training plan" },
  { icon: <Brain size={14} />, text: "Adjust plan based on my fatigue" },
  { icon: <Zap size={14} />, text: "What should I eat today?" },
  { icon: <Clock size={14} />, text: "Log today's workout" },
];

const SUGGESTIONS_ZH = [
  { icon: <Target size={14} />, text: "为我生成训练计划" },
  { icon: <Brain size={14} />, text: "根据我的疲劳状态调整计划" },
  { icon: <Zap size={14} />, text: "我今天应该怎么吃？" },
  { icon: <Clock size={14} />, text: "记录今天的训练" },
];

type Props = {
  messages: ChatMessage[];
  busy: boolean;
  session: SessionState | null;
  agentStatus: string;
  agentTrace: AgentTraceItem[];
  latestRunId: string | null;
  profileComplete: boolean;
  onSend: (text: string) => void;
  dashboard?: Dashboard | null;
  onNavigate?: (view: ViewName) => void;
};

export function ChatView({ messages, busy, session, agentStatus, agentTrace, latestRunId, onSend, onNavigate }: Props) {
  const { isZh } = useLanguage();
  const [input, setInput] = useState("");
  const [quickActionsOpen, setQuickActionsOpen] = useState(false);
  const [runDetail, setRunDetail] = useState<AgentRunDetail | null>(null);
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      messagesRef.current?.scrollTo({
        top: messagesRef.current.scrollHeight,
        behavior: "smooth",
      });
      bottomRef.current?.scrollIntoView({ block: "end" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, agentTrace, busy]);

  useEffect(() => {
    setRunDetail(null);
  }, [latestRunId]);

  const loadRunDetail = async () => {
    if (!latestRunId) return;
    setRunDetailLoading(true);
    try {
      setRunDetail(await fetchAgentRun(latestRunId));
    } catch {
      // ignore
    } finally {
      setRunDetailLoading(false);
    }
  };

  const handleSend = () => {
    if (!input.trim() || busy) return;
    onSend(input);
    setInput("");
  };

  return (
    <div className="chat-layout">
      <div className="chat-main">
        {/* header */}
        <div className="chat-header">
          <div>
            <h2>{session?.title || (isZh ? "训练工作台" : "Your training workspace")}</h2>
          </div>
        </div>

        {/* messages */}
        <div className="chat-messages" ref={messagesRef}>
          {session && <SubagentCatalogPanel sessionId={session.session_id} busy={busy} />}
          {messages.map((msg, i) => (
            <div key={i} className={`msg-row ${msg.role}`}>
              <div className="msg-avatar">
                {msg.role === "assistant" ? <Bot size={18} /> : <span>{isZh ? "你" : "You"}</span>}
              </div>
              <div className="msg-stack">
                {msg.role === "assistant" && !!msg.execution_events?.length && <ExecutionTimeline events={msg.execution_events} trace={i === messages.length - 1 ? agentTrace : []} busy={busy && i === messages.length - 1} status={i === messages.length - 1 ? agentStatus : ""} />}
                {msg.role === "assistant" && !msg.execution_events?.length && i === messages.length - 1 && (busy || agentTrace.length > 0) && (
                  <AgentProcessInline
                    trace={agentTrace}
                    busy={busy}
                    status={agentStatus}
                    latestRunId={latestRunId}
                    runDetail={runDetail}
                    runDetailLoading={runDetailLoading}
                    onLoadDetail={loadRunDetail}
                  />
                )}
                {msg.role === "assistant" && (msg.agent_run_id || (i === messages.length - 1 && latestRunId && !busy)) && <RecordedTracePanel key={`${session?.user_id || "anonymous"}:${msg.agent_run_id || latestRunId}`} runId={msg.agent_run_id || latestRunId!} ownerId={session?.user_id} />}
                <div className="msg-bubble">
                  {msg.content || (busy && msg.role === "assistant" && i === messages.length - 1 ? (
                    <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
                  ) : null)}
                </div>
              </div>
            </div>
          ))}
          {onNavigate && <PendingProposalPreview userId={session?.user_id} refreshKey={messages.length} onReview={() => onNavigate("responsibilities")} onViewPlan={() => onNavigate("plan")} />}
          <div ref={bottomRef} />
        </div>

        {/* suggestions */}
        {messages.length <= 1 && (
          <div className="suggestion-chips">
            {(isZh ? SUGGESTIONS_ZH : SUGGESTIONS).map((s, i) => (
              <button key={i} className="chip" onClick={() => onSend(s.text)} disabled={busy || !session}>
                {s.icon}
                <span>{s.text}</span>
              </button>
            ))}
          </div>
        )}

        {session && <FollowupPanel key={session.user_id} refreshKey={messages.length} />}

        {/* input */}
        <div className="chat-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleSend(); }
            }}
            aria-label={isZh ? "训练需求或问题" : "Training request or question"}
            placeholder={isZh ? "说说你的训练安排，或需要处理的记录…" : "Your training plans, or a record to work on..."}
            rows={1}
          />
          <button className="send-btn" aria-label={isZh ? "发送消息" : "Send message"} onClick={handleSend} disabled={busy || !session || !input.trim()}>
            <ArrowUp size={23} />
          </button>
          <button className="composer-actions-toggle" aria-label={isZh ? "训练快捷操作" : "Training shortcuts"} aria-expanded={quickActionsOpen} onClick={() => setQuickActionsOpen(value => !value)}>{quickActionsOpen ? <X size={22} /> : <Plus size={22} />}</button>
          <span className="composer-boundary" title={isZh ? "计划修改必须先审阅并明确确认；批准不代表执行完成。" : "Review and explicitly confirm plan changes. Approval is not execution."}>{isZh ? "修改需确认" : "Review changes"}<Info size={16} /></span>
          {quickActionsOpen && onNavigate && <div className="composer-actions-menu" role="group" aria-label={isZh ? "选择快捷操作" : "Choose a shortcut"}>
            {(["plan", "workout", "checkin", "responsibilities"] as ViewName[]).map((view, index) => <button key={view} onClick={() => { setQuickActionsOpen(false); onNavigate(view); }}>{(isZh ? ["查看训练安排", "记录或更正训练", "补充今天的状态", "查看长期跟踪"] : ["Training schedule", "Workout records", "Today's feedback", "Ongoing goals"])[index]}</button>)}
          </div>}
        </div>
      </div>
    </div>
  );
}

function AgentProcessInline({
  trace, busy, status, latestRunId, runDetail, runDetailLoading, onLoadDetail,
}: {
  trace: AgentTraceItem[]; busy: boolean; status: string; latestRunId: string | null;
  runDetail: AgentRunDetail | null; runDetailLoading: boolean; onLoadDetail: () => void;
}) {
  const { isZh } = useLanguage();
  return <ExecutionTimeline trace={trace} busy={busy} status={status}>
    {latestRunId && <details className="execution-run-detail">
      <summary onClick={() => { if (!runDetail && !runDetailLoading) onLoadDetail(); }}>{isZh ? "工具结果" : "Tool results"}</summary>
      {runDetailLoading && <p>{isZh ? "读取中…" : "Loading…"}</p>}
      {runDetail && <ul className="execution-tool-results">
        {runDetail.tool_calls.map((call, index) => <li key={index}>{call.tool_name} · {call.status}</li>)}
        {runDetail.error && <li>{runDetail.error}</li>}
        {!runDetail.tool_calls.length && <li>{isZh ? "没有已保存的工具调用记录。" : "No saved tool calls."}</li>}
      </ul>}
      {!runDetailLoading && !runDetail && <p>{isZh ? "尚未取得工具结果；可收起后重试。" : "Tool results unavailable; close and retry."}</p>}
    </details>}
  </ExecutionTimeline>;
}
