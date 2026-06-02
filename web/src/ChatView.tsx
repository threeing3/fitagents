import React, { useEffect, useRef, useState } from "react";
import { Bot, Send, Sparkles, ChevronRight, ChevronLeft, Clock, Zap, Brain, Target, ListChecks, Wrench, ShieldCheck, Activity } from "lucide-react";
import { fetchAgentRun } from "./api";
import type { SessionState, ChatMessage, AgentTraceItem, AgentRunDetail } from "./types";

const SUGGESTIONS = [
  { icon: <Target size={14} />, text: "Generate my training plan" },
  { icon: <Brain size={14} />, text: "Adjust plan based on my fatigue" },
  { icon: <Zap size={14} />, text: "What should I eat today?" },
  { icon: <Clock size={14} />, text: "Log today's workout" },
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
};

export function ChatView({ messages, busy, session, agentStatus, agentTrace, latestRunId, profileComplete, onSend }: Props) {
  const [input, setInput] = useState("");
  const [traceOpen, setTraceOpen] = useState(true);
  const [runDetailOpen, setRunDetailOpen] = useState(false);
  const [runDetail, setRunDetail] = useState<AgentRunDetail | null>(null);
  const [runDetailError, setRunDetailError] = useState("");
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const traceBottomRef = useRef<HTMLDivElement>(null);
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
    traceBottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [agentTrace]);

  useEffect(() => {
    setRunDetail(null);
    setRunDetailError("");
    setRunDetailOpen(false);
  }, [latestRunId]);

  const loadRunDetail = async () => {
    if (!latestRunId) return;
    setRunDetailOpen(true);
    if (runDetail?.id === latestRunId) return;
    setRunDetailLoading(true);
    setRunDetailError("");
    try {
      setRunDetail(await fetchAgentRun(latestRunId));
    } catch (error: any) {
      setRunDetailError(error?.message || "Failed to load agent run detail.");
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
      {/* ---- Messages panel ---- */}
      <div className="chat-main">
        <div className="chat-header">
          <Bot size={24} />
          <div>
            <h2>AI Coach</h2>
            <p>{profileComplete ? "Profile ready — generating personalized plans" : "Building your profile..."}</p>
          </div>
        </div>

        <div className="chat-messages" ref={messagesRef}>
          {messages.map((msg, i) => (
            <div key={i} className={`msg-row ${msg.role}`}>
              <div className="msg-avatar">
                {msg.role === "assistant" ? <Bot size={18} /> : <span>You</span>}
              </div>
              <div className="msg-stack">
                {msg.role === "assistant" && i === messages.length - 1 && (busy || agentTrace.length > 0) && (
                  <ThinkingProcess
                    trace={agentTrace}
                    busy={busy}
                    status={agentStatus}
                    latestRunId={latestRunId}
                  />
                )}
                <div className="msg-bubble">
                  {msg.content || (busy && msg.role === "assistant" && i === messages.length - 1 ? (
                    <span className="thinking-dots">
                      <span>.</span><span>.</span><span>.</span>
                    </span>
                  ) : null)}
                </div>
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Suggestions chips */}
        {messages.length <= 1 && (
          <div className="suggestion-chips">
            {SUGGESTIONS.map((s, i) => (
              <button key={i} className="chip" onClick={() => onSend(s.text)} disabled={busy || !session}>
                {s.icon}
                <span>{s.text}</span>
              </button>
            ))}
          </div>
        )}

        {/* Input */}
        <div className="chat-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Tell your coach about your goals, today's state, or ask for advice..."
            rows={2}
          />
          <button className="send-btn" onClick={handleSend} disabled={busy || !session || !input.trim()}>
            <Send size={20} />
          </button>
        </div>
      </div>

      {/* ---- Agent trace panel ---- */}
      <aside className={`trace-panel ${traceOpen ? "open" : "closed"}`}>
        <button className="trace-toggle" onClick={() => setTraceOpen((v) => !v)}>
          {traceOpen ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>

        {traceOpen && (
          <>
            <div className="trace-header">
              <Sparkles size={16} />
              <span>Agent Trace</span>
            </div>
            <div className="trace-status-line">
              <div className={`status-indicator ${busy ? "active" : "idle"}`} />
              <span>{agentStatus}</span>
              {latestRunId && <code>{latestRunId.slice(0, 8)}</code>}
            </div>
            {latestRunId && (
              <div className="run-detail-actions">
                <button type="button" onClick={loadRunDetail} disabled={runDetailLoading}>
                  {runDetailLoading ? "加载 Run Detail..." : runDetailOpen ? "刷新 Run Detail" : "查看 Run Detail"}
                </button>
                {runDetailOpen && (
                  <button type="button" onClick={() => setRunDetailOpen(false)}>
                    收起
                  </button>
                )}
              </div>
            )}
            {runDetailOpen && (
              <RunDetailPanel
                detail={runDetail}
                loading={runDetailLoading}
                error={runDetailError}
              />
            )}
            <div className="trace-list">
              {agentTrace.length === 0 ? (
                <div className="trace-empty">
                  <Sparkles size={20} />
                  <p>Send a message to see how the agent reads your profile, retrieves memories, matches rules, and generates responses.</p>
                </div>
              ) : (
                agentTrace.map((item) => (
                  <div key={item.id} className={`trace-node ${item.type}`}>
                    <div className="trace-node-head">
                      <strong>{item.title}</strong>
                      {item.latency_ms != null && <span className="trace-latency">{item.latency_ms}ms</span>}
                    </div>
                    <p>{item.summary}</p>
                    {item.metadata && Object.keys(item.metadata).length > 0 && (
                      <TraceChips meta={item.metadata} />
                    )}
                  </div>
                ))
              )}
              <div ref={traceBottomRef} />
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function RunDetailPanel({
  detail,
  loading,
  error,
}: {
  detail: AgentRunDetail | null;
  loading: boolean;
  error: string;
}) {
  const memoryVerifyNode = detail?.nodes?.find((node) => node.node === "MemoryVerifier");
  const memoryVerifyOutput = memoryVerifyNode?.output || {};
  return (
    <div className="run-detail-panel">
      <div className="run-detail-head">
        <strong>Run Detail Debug</strong>
        {detail && <span>{detail.status}</span>}
      </div>
      {loading && <p className="run-detail-muted">正在读取 agent run trace...</p>}
      {error && <p className="run-detail-error">{error}</p>}
      {detail && (
        <>
          <div className="run-detail-grid">
            <span>Run</span>
            <code>{detail.id.slice(0, 8)}</code>
            <span>Type</span>
            <code>{detail.run_type}</code>
            <span>Nodes</span>
            <code>{detail.nodes.length}</code>
            <span>Tools</span>
            <code>{detail.tool_calls.length}</code>
          </div>
          {detail.log_path && (
            <div className="run-detail-log">
              <span>日志</span>
              <code>{detail.log_path}</code>
            </div>
          )}
          {memoryVerifyNode && (
            <div className="run-detail-section memory-verify">
              <h4>Memory Verify</h4>
              <div className="run-detail-metrics">
                <span>accepted={memoryVerifyOutput.accepted_count ?? 0}</span>
                <span>rejected={memoryVerifyOutput.rejected_count ?? 0}</span>
                <span>issues={memoryVerifyOutput.issue_count ?? 0}</span>
              </div>
              {(memoryVerifyOutput.issues || []).slice(0, 4).map((issue: any, index: number) => (
                <div key={`${issue.issue_id || "issue"}-${index}`} className="run-detail-issue">
                  <strong>{issue.issue_id || "issue"}</strong>
                  <span>{issue.severity || "unknown"} / {issue.action || "review"}</span>
                  <p>{issue.message || ""}</p>
                </div>
              ))}
            </div>
          )}
          <div className="run-detail-section">
            <h4>Nodes</h4>
            {detail.nodes.slice(0, 12).map((node, index) => (
              <details key={node.event_id || `${node.node}-${index}`} className="run-detail-node">
                <summary>
                  <span>{node.node || "Node"}</span>
                  <code>{node.latency_ms ?? 0}ms</code>
                </summary>
                <pre>{compactJson(node.output || node)}</pre>
              </details>
            ))}
          </div>
          <div className="run-detail-section">
            <h4>Tool Calls</h4>
            {detail.tool_calls.length === 0 ? (
              <p className="run-detail-muted">没有持久化 tool call。</p>
            ) : (
              detail.tool_calls.slice(0, 12).map((call, index) => (
                <details key={call.id || `${call.tool_name}-${index}`} className="run-detail-node">
                  <summary>
                    <span>{call.tool_name || "tool"}</span>
                    <code>{call.status || "unknown"} · {call.latency_ms ?? 0}ms</code>
                  </summary>
                  <pre>{compactJson({ input: call.input_json, output: call.output_json })}</pre>
                </details>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

function compactJson(value: any) {
  return JSON.stringify(value ?? {}, null, 2).slice(0, 2200);
}

function ThinkingProcess({
  trace,
  busy,
  status,
  latestRunId,
}: {
  trace: AgentTraceItem[];
  busy: boolean;
  status: string;
  latestRunId: string | null;
}) {
  const phases = buildThinkingPhases(trace, busy, status);
  if (phases.length === 0 && !busy) return null;
  return (
    <div className="thinking-process" aria-label="Agent execution process">
      <div className="thinking-process-head">
        <div className="thinking-process-title">
          <Sparkles size={14} />
          <span>{busy ? "Agent 正在思考" : "Agent 执行过程"}</span>
        </div>
        {latestRunId && <code>{latestRunId.slice(0, 8)}</code>}
      </div>
      <div className="thinking-process-list">
        {phases.map((phase) => (
          <div key={phase.key} className={`thinking-step ${phase.state}`}>
            <span className="thinking-step-icon">{phase.icon}</span>
            <span className="thinking-step-copy">
              <strong>{phase.title}</strong>
              <em>{phase.summary}</em>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function buildThinkingPhases(trace: AgentTraceItem[], busy: boolean, status: string) {
  const hasTitle = (needle: string) => trace.some((item) => item.title === needle);
  const hasTool = (toolName: string) => trace.some((item) => item.metadata?.tool_name === toolName || item.summary.includes(toolName));
  const latestStatus = [...trace].reverse().find((item) => item.type === "status")?.summary || status;
  const phases = [
    {
      key: "planner",
      title: "规划本轮任务",
      summary: hasTitle("AgentPlanner") ? "已确定目标、步骤和当前消息优先策略" : latestStatus || "等待 Planner 输出",
      state: hasTitle("AgentPlanner") ? "done" : busy ? "active" : "pending",
      icon: <ListChecks size={14} />,
    },
    {
      key: "executor",
      title: "调用工具执行",
      summary: hasTool("context.build") ? "已读取档案、记忆、知识、规则和模板" : "准备执行建档、记忆和上下文工具",
      state: hasTool("context.build") ? "done" : hasTitle("AgentPlanner") ? "active" : "pending",
      icon: <Activity size={14} />,
    },
    {
      key: "verifier",
      title: "自检输出约束",
      summary: hasTitle("ResponseVerifier") ? "已检查当前回复是否符合安全、结构和当前请求约束" : "等待回复生成后检查",
      state: hasTitle("ResponseVerifier") ? "done" : hasTool("plan.verify") ? "active" : "pending",
      icon: <ShieldCheck size={14} />,
    },
    {
      key: "repair",
      title: "必要时修复",
      summary: hasTitle("PlanRepair") || hasTitle("ResponseRepair") ? "已执行确定性修复" : "没有发现必须自动修复的问题",
      state: hasTitle("PlanRepair") || hasTitle("ResponseRepair") ? "done" : hasTitle("ResponseVerifier") ? "done" : "pending",
      icon: <Wrench size={14} />,
    },
  ];
  return phases;
}

function TraceChips({ meta }: { meta: Record<string, any> }) {
  const chips: string[] = [];
  if (meta.provider) chips.push(`provider=${meta.provider}`);
  if (meta.chat_model) chips.push(`model=${meta.chat_model}`);
  if (meta.embedding_mode) chips.push(`embed=${meta.embedding_mode}`);
  if (meta.intent) chips.push(`intent=${meta.intent}`);
  if (meta.tool_name) chips.push(`tool=${meta.tool_name}`);
  if (meta.status) chips.push(`status=${meta.status}`);
  if (meta.timeline_id) chips.push("timeline");
  if (Array.isArray(meta.missing_slots) && meta.missing_slots.length) chips.push(`missing=${meta.missing_slots.join("/")}`);
  if (Array.isArray(meta.matched_rule_ids) && meta.matched_rule_ids.length) chips.push(`rules=${meta.matched_rule_ids.length}`);
  if (Array.isArray(meta.matched_template_ids) && meta.matched_template_ids.length) chips.push(`templates=${meta.matched_template_ids.length}`);
  if (Array.isArray(meta.matched_knowledge_ids) && meta.matched_knowledge_ids.length) chips.push(`knowledge=${meta.matched_knowledge_ids.length}`);
  if (meta.log_path) chips.push("log saved");
  if (chips.length === 0) return null;
  return (
    <div className="trace-chips">
      {chips.slice(0, 5).map((c) => (
        <span key={c}>{c}</span>
      ))}
    </div>
  );
}
