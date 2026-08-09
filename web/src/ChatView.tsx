import React, { useEffect, useRef, useState } from "react";
import { Bot, Send, Sparkles, ChevronDown, ChevronRight, Clock, Zap, Brain, Target, Activity, ShieldCheck, Wrench, CheckCircle, XCircle } from "lucide-react";
import { fetchAgentRun } from "./api";
import type { SessionState, ChatMessage, AgentTraceItem, AgentRunDetail } from "./types";
import { useLanguage } from "./LanguageContext";

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
};

export function ChatView({ messages, busy, session, agentStatus, agentTrace, latestRunId, profileComplete, onSend }: Props) {
  const { isZh } = useLanguage();
  const [input, setInput] = useState("");
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
          <Bot size={24} />
          <div>
            <h2>AI Coach</h2>
            <p>{profileComplete ? (isZh ? "档案已就绪" : "Profile ready") : (isZh ? "正在建立档案…" : "Building your profile...")}</p>
          </div>
        </div>

        {/* messages */}
        <div className="chat-messages" ref={messagesRef}>
          {messages.map((msg, i) => (
            <div key={i} className={`msg-row ${msg.role}`}>
              <div className="msg-avatar">
                {msg.role === "assistant" ? <Bot size={18} /> : <span>{isZh ? "你" : "You"}</span>}
              </div>
              <div className="msg-stack">
                {msg.role === "assistant" && i === messages.length - 1 && (busy || agentTrace.length > 0) && (
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
                <div className="msg-bubble">
                  {msg.content || (busy && msg.role === "assistant" && i === messages.length - 1 ? (
                    <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
                  ) : null)}
                </div>
              </div>
            </div>
          ))}
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

        {/* input */}
        <div className="chat-composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
            }}
            placeholder={isZh ? "告诉教练你的目标、今天的状态，或提出问题……" : "Tell your coach about your goals, today's state, or ask for advice..."}
            rows={2}
          />
          <button className="send-btn" onClick={handleSend} disabled={busy || !session || !input.trim()}>
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Inline agent process card — Claude Code style
// ============================================================================

type Phase = {
  stage: string;
  icon: React.ReactNode;
  label: string;
  steps: StepState[];
};

type StepState = {
  tool_name: string;
  description: string;
  status: "pending" | "running" | "done" | "error";
  latency_ms?: number;
  detail?: string;
};

function buildPhases(trace: AgentTraceItem[], busy: boolean, status: string, isZh: boolean): { phases: Phase[]; activeStage: string | null } {
  const steps = trace.filter(t => t.type === "step");
  const stepMap = new Map<string, StepState>();

  for (const s of steps) {
    const tool = s.metadata?.tool_name || s.title;
    const key = tool;
    if (!stepMap.has(key)) {
      stepMap.set(key, {
        tool_name: key,
        description: s.summary,
        status: "done",
        latency_ms: s.latency_ms,
        detail: s.metadata?.output_summary ? JSON.stringify(s.metadata.output_summary).slice(0, 120) : undefined,
      });
    }
  }

  // If busy and no completed steps yet, mark the first expected steps as pending/running
  if (busy && steps.length === 0) {
    stepMap.set("planning", { tool_name: "planning", description: isZh ? "正在规划执行步骤…" : "Planning execution steps...", status: "running" });
  }

  const result: Phase[] = [];
  const ordered = Array.from(stepMap.values());

  // Group into phases
  const plannerSteps = ordered.filter(s => ["AgentPlanner", "ToolRegistry", "AgentTaskTimeline", "profile.extract", "memory.verify", "memory.write"].includes(s.tool_name));
  const executorSteps = ordered.filter(s => ["context.build", "plan.decide", "plan.generate", "plan.verify", "plan.repair", "coach.reply", "ContextBuilder", "CoachLLM"].includes(s.tool_name));
  const verifierSteps = ordered.filter(s => ["response.verify", "response.repair", "guardrail.check", "ResponseVerifier", "ResponseRepair", "GuardrailCheck"].includes(s.tool_name));

  if (plannerSteps.length > 0) {
    result.push({ stage: "plan", icon: <Activity size={12} />, label: isZh ? "规划与档案" : "Plan & Profile", steps: plannerSteps });
  }
  if (executorSteps.length > 0) {
    result.push({ stage: "execute", icon: <Zap size={12} />, label: isZh ? "上下文与回复" : "Context & Reply", steps: executorSteps });
  }
  if (verifierSteps.length > 0) {
    result.push({ stage: "verify", icon: <ShieldCheck size={12} />, label: isZh ? "校验与修复" : "Verify & Repair", steps: verifierSteps });
  }

  // Determine active stage
  let activeStage: string | null = null;
  if (busy) {
    for (const phase of result) {
      if (phase.steps.some(s => s.status === "running")) { activeStage = phase.stage; break; }
    }
    if (!activeStage && result.length > 0) activeStage = result[result.length - 1].stage;
  }

  return { phases: result, activeStage };
}

function AgentProcessInline({
  trace,
  busy,
  status,
  latestRunId,
  runDetail,
  runDetailLoading,
  onLoadDetail,
}: {
  trace: AgentTraceItem[];
  busy: boolean;
  status: string;
  latestRunId: string | null;
  runDetail: AgentRunDetail | null;
  runDetailLoading: boolean;
  onLoadDetail: () => void;
}) {
  const { isZh } = useLanguage();
  const [expanded, setExpanded] = useState(true);
  const [showDetail, setShowDetail] = useState(false);

  if (trace.length === 0 && !busy) return null;

  const { phases, activeStage } = buildPhases(trace, busy, status, isZh);
  const totalSteps = phases.reduce((sum, p) => sum + p.steps.length, 0);
  const doneSteps = phases.reduce((sum, p) => sum + p.steps.filter(s => s.status === "done").length, 0);

  return (
    <div className="agent-process-inline" role="status" aria-label={isZh ? "智能体执行进度" : "Agent execution progress"}>
      {/* header bar */}
      <button
        className="agent-process-bar"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        <span className="agent-process-bar-left">
          <Sparkles size={13} />
          <span className="agent-process-bar-title">
            {busy ? (isZh ? "智能体思考中" : "Agent thinking") : (isZh ? "智能体执行过程" : "Agent process")}
          </span>
          <span className="agent-process-bar-count">
            {doneSteps}/{totalSteps} {isZh ? "步" : "steps"}
          </span>
        </span>
        <span className="agent-process-bar-right">
          {busy && <span className="agent-process-status">{status}</span>}
          {latestRunId && (
            <code className="agent-process-run-id">{latestRunId.slice(0, 8)}</code>
          )}
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {/* progress mini-bar */}
      {totalSteps > 0 && (
        <div className="agent-process-progress">
          <div
            className="agent-process-progress-fill"
            style={{ width: `${(doneSteps / totalSteps) * 100}%` }}
          />
        </div>
      )}

      {/* expanded steps */}
      {expanded && (
        <div className="agent-process-steps">
          {phases.map((phase) => (
            <div key={phase.stage} className={`agent-phase ${activeStage === phase.stage ? "active" : ""}`}>
              <div className="agent-phase-head">
                <span className={`agent-phase-icon ${activeStage === phase.stage ? "pulse" : ""}`}>
                  {phase.icon}
                </span>
                <span className="agent-phase-label">{phase.label}</span>
              </div>
              <div className="agent-phase-steps">
                {phase.steps.map((step) => (
                  <div key={step.tool_name} className={`agent-step ${step.status}`}>
                    <span className="agent-step-icon">
                      {step.status === "done" ? (
                        <CheckCircle size={11} />
                      ) : step.status === "error" ? (
                        <XCircle size={11} />
                      ) : step.status === "running" ? (
                        <span className="spinner-mini" />
                      ) : (
                        <span className="dot-mini" />
                      )}
                    </span>
                    <span className="agent-step-name">{step.description}</span>
                    {step.latency_ms != null && step.latency_ms > 0 && (
                      <span className="agent-step-latency">{step.latency_ms}ms</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}

          {/* detail toggle */}
          {latestRunId && phases.length > 0 && (
            <div className="agent-process-actions">
              <button
                type="button"
                className="agent-detail-btn"
                onClick={() => { setShowDetail(v => !v); if (!runDetail) onLoadDetail(); }}
                disabled={runDetailLoading}
              >
                {runDetailLoading ? (isZh ? "读取详情…" : "Loading detail...") : showDetail ? (isZh ? "隐藏详情" : "Hide detail") : (isZh ? "查看详情" : "View detail")}
              </button>
            </div>
          )}
        </div>
      )}

      {/* run detail (collapsible debug view) */}
      {showDetail && runDetail && (
        <div className="agent-run-detail">
          <div className="agent-run-detail-head">
            <span>Run {runDetail.id.slice(0, 8)}</span>
            <span>{runDetail.run_type} · {runDetail.status} · {runDetail.nodes.length} {isZh ? "个节点" : "nodes"}</span>
          </div>
          {runDetail.tool_calls.length > 0 && (
            <div className="agent-run-detail-tools">
              {runDetail.tool_calls.slice(0, 8).map((call, i) => (
                <div key={i} className="agent-run-detail-tool">
                  <span className="agent-run-detail-tool-name">{call.tool_name}</span>
                  <span className="agent-run-detail-tool-meta">{call.status} · {call.latency_ms ?? 0}ms</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
