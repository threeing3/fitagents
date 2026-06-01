import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  MessageCircle,
  LayoutDashboard,
  ClipboardCheck,
  Dumbbell,
  Sparkles,
  Zap,
  LogOut,
  UserCircle,
} from "lucide-react";
import type { SessionState, Dashboard, ChatMessage, AgentTraceItem, ViewName } from "./types";
import { createSession, fetchDashboard, fetchSessionMessages, listSessions, pause, streamChat } from "./api";
import { ChatView } from "./ChatView";
import { DashboardView } from "./DashboardView";
import { CheckinView } from "./CheckinView";
import { AccountView } from "./AccountView";
import { AuthProvider, useAuth } from "./AuthContext";
import { LoginView } from "./LoginView";
import "./styles.css";

const TYPEWRITER_DELAY_MS = 16;
const INTRO_MESSAGE: ChatMessage = {
  role: "assistant",
  content: "Hi, I'm your AI fitness coach. Tell me your age, height, weight, goals, training experience, and available equipment - I'll build your profile and create a personalized plan.",
};

function AppContent() {
  const auth = useAuth();
  const [session, setSession] = useState<SessionState | null>(null);
  const [activeView, setActiveView] = useState<ViewName>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([INTRO_MESSAGE]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [agentStatus, setAgentStatus] = useState("Ready");
  const [agentTrace, setAgentTrace] = useState<AgentTraceItem[]>([]);
  const [latestRunId, setLatestRunId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // ---- session init (runs after auth is ready) ----
  useEffect(() => {
    if (!auth.user) return;
    let cancelled = false;
    const storageKey = `ai_fitness_active_session_${auth.user.user_id}`;

    async function restoreSession() {
      try {
        setNotice("");
        const sessions = await listSessions();
        const savedSessionId = localStorage.getItem(storageKey);
        let active =
          sessions.find((item) => item.session_id === savedSessionId) ||
          sessions[0] ||
          null;

        if (!active) {
          active = await createSession(auth.user?.display_name || "Fitness User");
        }

        if (cancelled) return;
        localStorage.setItem(storageKey, active.session_id);
        setSession({
          session_id: active.session_id,
          user_id: active.user_id,
          title: active.title,
          created_at: active.created_at,
        });

        const history = await fetchSessionMessages(active.session_id);
        if (cancelled) return;
        setMessages(history.length > 0 ? history : [INTRO_MESSAGE]);
        if (history.length > 0) {
          setNotice(`Loaded ${history.length} saved messages from your last session.`);
        }
      } catch (error: any) {
        if (!cancelled) setNotice(`Backend unavailable: ${error.message}`);
      }
    }

    restoreSession();
    return () => {
      cancelled = true;
    };
  }, [auth.user]);

  useEffect(() => {
    if (session) refreshDashboard(session.user_id);
  }, [session]);

  async function refreshDashboard(userId: string) {
    try {
      const data = await fetchDashboard(userId);
      setDashboard(data);
    } catch {
      setDashboard(null);
    }
  }

  // ---- send message ----
  const sendMessage = useCallback(
    async (text: string) => {
      if (!session || !text.trim()) return;
      const userText = text.trim();
      setBusy(true);
      setNotice("");
      setAgentStatus("Thinking...");
      setAgentTrace([]);
      setLatestRunId(null);
      setMessages((prev) => [
        ...prev,
        { role: "user", content: userText },
        { role: "assistant", content: "" },
      ]);

      let assistantText = "";
      const appendChars = async (text: string) => {
        for (const char of [...text]) {
          assistantText += char;
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, content: assistantText };
            return next;
          });
          await pause(TYPEWRITER_DELAY_MS);
        }
      };

      const pushTrace = (item: Omit<AgentTraceItem, "id">) => {
        setAgentTrace((prev) => [...prev, { ...item, id: `${Date.now()}-${prev.length}-${item.type}` }]);
      };

      try {
        const response = await streamChat(session.session_id, session.user_id, userText);
        if (!response.ok) throw new Error(await response.text());
        if (!response.body) throw new Error("Streaming not supported.");

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let pending = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          pending += decoder.decode(value, { stream: true });
          const lines = pending.split("\n");
          pending = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const event = JSON.parse(line);
              if (event.type === "answer_delta") await appendChars(String(event.text || ""));
              else if (event.type === "status") {
                setAgentStatus(String(event.text || "Processing..."));
                pushTrace({ type: "status", title: "Status", summary: String(event.text || ""), metadata: compactMeta(event) });
              } else if (event.type === "step") {
                pushTrace({ type: "step", title: String(event.name || "Step"), summary: String(event.summary || ""), latency_ms: event.latency_ms, metadata: event.metadata || {} });
              } else if (event.type === "tool_call") {
                pushTrace({ type: "tool_call", title: String(event.name || "Tool"), summary: String(event.summary || event.status || ""), metadata: event.metadata || {} });
              } else if (event.type === "error") {
                pushTrace({ type: "error", title: "Error", summary: String(event.summary || event.message || "") });
              } else if (event.type === "done") {
                setLatestRunId(event.run_id || null);
                setAgentStatus("Done");
                pushTrace({ type: "done", title: "Complete", summary: event.run_id ? `Run ${event.run_id.slice(0, 8)}` : "Done", metadata: { log_path: event.log_path, tool_calls: event.tool_calls || [] } });
              }
            } catch {
              await appendChars(line);
            }
          }
        }

        const remaining = pending.trim();
        if (remaining) {
          try {
            const event = JSON.parse(remaining);
            if (event.type === "answer_delta") await appendChars(String(event.text || ""));
          } catch {
            await appendChars(remaining);
          }
        }

        if (!assistantText.trim()) {
          await appendChars("I've recorded your input. Tell me more about your goals, training conditions, or how you're feeling today.");
        }
        setNotice("Response saved. Agent continues with your profile & memory.");
        setAgentStatus("Done");
        await refreshDashboard(session.user_id);
      } catch (err: any) {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") next[next.length - 1] = { ...last, content: `Request failed: ${err.message}` };
          return next;
        });
      } finally {
        setBusy(false);
      }
    },
    [session],
  );

  // ---- dashboard derived ----
  const todayExercises = useMemo(() => {
    const ex = dashboard?.today_plan?.exercises;
    return Array.isArray(ex) ? ex : [];
  }, [dashboard]);

  const profileComplete = dashboard?.profile_complete ?? false;

  // ---- auth loading ----
  if (auth.loading) {
    return (
      <div className="auth-loading">
        <Dumbbell size={36} className="auth-loading-icon" />
        <span>Loading...</span>
      </div>
    );
  }

  // ---- unauthenticated ----
  if (!auth.user) {
    return <LoginView />;
  }

  // ---- authenticated app ----
  return (
    <div className="app-root">
      {/* ---- Sidebar ---- */}
      <aside className={`sidebar ${sidebarOpen ? "open" : "closed"}`}>
        <div className="sidebar-brand" onClick={() => setSidebarOpen((v) => !v)}>
          <Zap size={24} />
          {sidebarOpen && <span>AI Coach</span>}
        </div>

        <nav className="sidebar-nav">
          <NavItem icon={<MessageCircle size={20} />} label="Chat" active={activeView === "chat"} onClick={() => setActiveView("chat")} collapsed={!sidebarOpen} />
          <NavItem icon={<LayoutDashboard size={20} />} label="Dashboard" active={activeView === "dashboard"} onClick={() => setActiveView("dashboard")} collapsed={!sidebarOpen} />
          <NavItem icon={<ClipboardCheck size={20} />} label="Check-in" active={activeView === "checkin"} onClick={() => setActiveView("checkin")} collapsed={!sidebarOpen} />
          <NavItem icon={<UserCircle size={20} />} label="Account" active={activeView === "account"} onClick={() => setActiveView("account")} collapsed={!sidebarOpen} />
        </nav>

        <div className="sidebar-footer">
          <div className="session-badge">
            <div className={`status-dot ${session ? "live" : "dead"}`} />
            {sidebarOpen && <span>{session ? "Session live" : "Connecting..."}</span>}
          </div>
          {sidebarOpen && (
            <div className="sidebar-user">
              <button className="sidebar-user-profile" onClick={() => setActiveView("account")}>
                <span className="sidebar-avatar">
                  {auth.user.avatar_url ? (
                    <img src={auth.user.avatar_url} alt="" />
                  ) : (
                    auth.user.display_name.slice(0, 2).toUpperCase()
                  )}
                </span>
                <span className="sidebar-user-copy">
                  <span className="sidebar-user-name">{auth.user.display_name}</span>
                  <span className="sidebar-user-email">{auth.user.email}</span>
                </span>
              </button>
              <button className="logout-btn" onClick={auth.logout} title="Sign out">
                <LogOut size={14} />
              </button>
            </div>
          )}
          {!sidebarOpen && (
            <button className="logout-btn icon-only" onClick={auth.logout} title="Sign out">
              <LogOut size={14} />
            </button>
          )}
        </div>
      </aside>

      {/* ---- Main ---- */}
      <div className="main-area">
        {/* Notice bar */}
        {notice && (
          <div className="notice-bar">
            <Sparkles size={14} />
            <span>{notice}</span>
          </div>
        )}

        {/* Views */}
        {activeView === "chat" && (
          <ChatView
            messages={messages}
            busy={busy}
            session={session}
            agentStatus={agentStatus}
            agentTrace={agentTrace}
            latestRunId={latestRunId}
            profileComplete={profileComplete}
            onSend={sendMessage}
          />
        )}

        {activeView === "dashboard" && (
          <DashboardView
            dashboard={dashboard}
            session={session}
            busy={busy}
            onRefresh={() => session && refreshDashboard(session.user_id)}
          />
        )}

        {activeView === "checkin" && (
          <CheckinView
            session={session}
            busy={busy}
            setBusy={setBusy}
            setNotice={setNotice}
            onRefresh={() => session && refreshDashboard(session.user_id)}
          />
        )}

        {activeView === "account" && <AccountView />}
      </div>
    </div>
  );
}

// ---- sidebar nav item ----
function NavItem({
  icon,
  label,
  active,
  onClick,
  collapsed,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  collapsed: boolean;
}) {
  return (
    <button
      className={`nav-item ${active ? "active" : ""}`}
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-label={label}
    >
      <span className="nav-icon">{icon}</span>
      {!collapsed && <span className="nav-label">{label}</span>}
    </button>
  );
}

// ---- helpers ----
function compactMeta(event: Record<string, any>): Record<string, any> {
  const { type, text, summary, message, ...rest } = event;
  return rest;
}

// ---- mount ----
function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
