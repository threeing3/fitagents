import React, { useEffect, useRef, useState } from "react";
import { Bot, Send, Sparkles, ChevronRight, ChevronLeft, Clock, Zap, Brain, Target } from "lucide-react";
import type { SessionState, ChatMessage, AgentTraceItem } from "./types";

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
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

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

        <div className="chat-messages">
          {messages.map((msg, i) => (
            <div key={i} className={`msg-row ${msg.role}`}>
              <div className="msg-avatar">
                {msg.role === "assistant" ? <Bot size={18} /> : <span>You</span>}
              </div>
              <div className="msg-bubble">
                {msg.content || (busy && msg.role === "assistant" && i === messages.length - 1 ? (
                  <span className="thinking-dots">
                    <span>.</span><span>.</span><span>.</span>
                  </span>
                ) : null)}
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
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

function TraceChips({ meta }: { meta: Record<string, any> }) {
  const chips: string[] = [];
  if (meta.provider) chips.push(`provider=${meta.provider}`);
  if (meta.chat_model) chips.push(`model=${meta.chat_model}`);
  if (meta.embedding_mode) chips.push(`embed=${meta.embedding_mode}`);
  if (meta.intent) chips.push(`intent=${meta.intent}`);
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
