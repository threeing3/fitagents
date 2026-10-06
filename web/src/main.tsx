import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Languages,
  PanelLeft,
  CalendarDays,
  ChartNoAxesColumn,
  TrendingUp,
  Settings,
} from "lucide-react";
import type { SessionState, Dashboard, ChatMessage, AgentTraceItem, ViewName, UsageSummary } from "./types";
import { createSession, fetchChatRequestStatus, fetchDashboard, fetchSessionMessages, fetchUsageSummary, listSessions, pause, streamChat } from "./api";
import { ChatView } from "./ChatView";
import { publicTraceMetadata } from "./ExecutionTimeline";
import { ChatRequestLedger } from "./chatRequestLedger";
import { DashboardView } from "./DashboardView";
import { ResponsibilityView } from "./ResponsibilityView";
import { createResponsibilityDemo } from "./responsibilityDemo";
import { CheckinView } from "./CheckinView";
import { TrainingWorkspace } from "./TrainingWorkspace";
import { SessionHistory } from "./SessionHistory";
import { SettingsView } from "./SettingsView";
import { AuthProvider, useAuth } from "./AuthContext";
import { LoginView } from "./LoginView";
import { LanguageProvider, useLanguage } from "./LanguageContext";
import "./styles.css";
import "./workspace.css";
import "./harness.css";

const TYPEWRITER_DELAY_MS = 16;
function introMessage(isZh: boolean): ChatMessage {
  return {
    role: "assistant",
    content: isZh
      ? "这里是你的训练工作台。目标和安排由你决定，我可以整理训练记录、提供候选方案，并协助完成你确认的操作。先告诉我你的目标、训练经验和可用器械；需要补充的信息，我们再逐步确认。"
      : "Your training, your decisions. I can organize your records, propose options and assist with actions you authorize. Start with your goals, experience and available equipment.",
  };
}

function AppContent() {
  const auth = useAuth();
  const { isZh, language, setLanguage } = useLanguage();
  const [session, setSession] = useState<SessionState | null>(null);
  const [activeView, setActiveView] = useState<ViewName>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([introMessage(isZh)]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const [notice, setNotice] = useState("");
  const [agentStatus, setAgentStatus] = useState("Ready");
  const [agentTrace, setAgentTrace] = useState<AgentTraceItem[]>([]);
  const [latestRunId, setLatestRunId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 768);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [sessionLoading, setSessionLoading] = useState(false);
  const sessionEpoch = useRef(0);
  const currentOwner = useRef(auth.user?.user_id);
  currentOwner.current = auth.user?.user_id;
  useEffect(() => {
    if (window.innerWidth <= 768) setSidebarOpen(false);
  }, [activeView]);

  // ---- session init (runs after auth is ready) ----
  useEffect(() => {
    if (!auth.user) return;
    let cancelled = false;
    const epoch = ++sessionEpoch.current;
    setSession(null);
    setMessages([introMessage(isZh)]);
    setAgentTrace([]);
    setLatestRunId(null);
    setDashboard(null);
    setSessionLoading(true);
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

        if (cancelled || epoch !== sessionEpoch.current) return;
        localStorage.setItem(storageKey, active.session_id);
        setSession({
          session_id: active.session_id,
          user_id: active.user_id,
          title: active.title,
          created_at: active.created_at,
        });

        const history = await fetchSessionMessages(active.session_id);
        if (cancelled || epoch !== sessionEpoch.current) return;
        setMessages(history.length > 0 ? history : [introMessage(isZh)]);
      } catch (error: any) {
        if (!cancelled) setNotice(isZh ? `服务暂不可用：${error.message}` : `Backend unavailable: ${error.message}`);
      } finally {
        if (!cancelled && epoch === sessionEpoch.current) setSessionLoading(false);
      }
    }

    restoreSession();
    return () => {
      cancelled = true;
    };
  }, [auth.user, isZh]);

  async function selectSession(value: SessionState) {
    if (busy || sessionLoading || value.user_id !== currentOwner.current) return;
    if (window.innerWidth <= 768) setSidebarOpen(false);
    if (value.session_id === session?.session_id) { setActiveView("chat"); return; }
    const epoch = ++sessionEpoch.current;
    setSessionLoading(true);
    try {
      const history = await fetchSessionMessages(value.session_id);
      if (epoch !== sessionEpoch.current || value.user_id !== currentOwner.current) return;
      setSession(value);
      setMessages(history.length ? history : [introMessage(isZh)]);
      setAgentTrace([]); setLatestRunId(null); setNotice("");
      setActiveView("chat");
      localStorage.setItem(`ai_fitness_active_session_${value.user_id}`, value.session_id);
    } catch {
      if (epoch === sessionEpoch.current) setNotice(isZh ? "会话读取失败，当前对话仍保留。" : "Conversation unavailable. Current conversation retained.");
    } finally {
      if (epoch === sessionEpoch.current) setSessionLoading(false);
    }
  }

  useEffect(() => {
    if (!auth.user) return;
    fetchUsageSummary().then(setUsage).catch(() => setUsage(null));
  }, [auth.user]);

  useEffect(() => {
    if (session) refreshDashboard(session.user_id);
  }, [session]);

  async function refreshDashboard(userId: string) {
    try {
      const data = await fetchDashboard(userId);
      if (userId === currentOwner.current) setDashboard(data);
    } catch {
      if (userId === currentOwner.current) setDashboard(null);
    }
  }

  // ---- send message ----
  const sendMessage = useCallback(
    async (text: string) => {
      if (!session || !text.trim() || sending.current) return;
      const userText = text.trim();
      const requestIdentity = JSON.stringify([session.user_id, session.session_id, userText]);
      let requestKey: string;
      let requestLedger: ChatRequestLedger;
      try {
        requestLedger = new ChatRequestLedger(window.sessionStorage);
        requestKey = requestLedger.getOrCreate(requestIdentity);
      } catch {
        setNotice(isZh ? "无法安全保存或恢复请求标识，已停止发送以避免重复记录。请检查浏览器会话存储。" : "Unable to preserve request identity. Send stopped to prevent duplicate records.");
        return;
      }
      sending.current = true;
      let receivedDone = false;
      setBusy(true);
      setNotice("");
      setAgentStatus(isZh ? "正在思考…" : "Thinking...");
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
        const response = await streamChat(session.session_id, session.user_id, userText, requestKey);
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
              else if (event.type === "execution_event") {
                setMessages(prev => {
                  const next = [...prev];
                  const last = next[next.length - 1];
                  if (last?.role === "assistant") next[next.length - 1] = { ...last, execution_events: [...(last.execution_events || []), event] };
                  return next;
                });
              } else if (event.type === "status") {
                setAgentStatus(String(event.text || "Processing..."));
                pushTrace({ type: "status", title: "Status", summary: String(event.text || ""), metadata: compactMeta(event) });
              } else if (event.type === "step") {
                pushTrace({ type: "step", title: String(event.name || "Step"), summary: String(event.summary || ""), latency_ms: event.latency_ms, metadata: publicTraceMetadata(event) });
              } else if (event.type === "tool_call") {
                pushTrace({ type: "tool_call", title: String(event.name || "Tool"), summary: String(event.summary || event.status || ""), metadata: publicTraceMetadata(event) });
              } else if (event.type === "error") {
                pushTrace({ type: "error", title: "Error", summary: String(event.summary || event.message || "") });
              } else if (event.type === "done") {
                receivedDone = true;
                setLatestRunId(event.run_id || null);
                setMessages(prev => {
                  const next = [...prev];
                  const last = next[next.length - 1];
                  if (last?.role === "assistant") next[next.length - 1] = { ...last, agent_run_id: event.run_id || null };
                  return next;
                });
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

        if (!receivedDone) throw new Error(isZh ? "未收到完成确认，请重发原消息查询结果；不要更换内容重复记录。" : "Completion was not confirmed. Retry the same message to retrieve its result.");
        requestLedger.complete(requestIdentity, requestKey);
        if (!assistantText.trim()) {
          await appendChars(isZh ? "我已记录你的输入。可以继续告诉我目标、训练条件或今天的状态。" : "I've recorded your input. Tell me more about your goals, training conditions, or how you're feeling today.");
        }
        setNotice(isZh ? "回复已保存，智能体会继续维护档案与记忆。" : "Response saved. Agent continues with your profile & memory.");
        setAgentStatus(isZh ? "完成" : "Done");
        await refreshDashboard(session.user_id);
        fetchUsageSummary().then(setUsage).catch(() => setUsage(null));
      } catch (err: any) {
        // This is a read-only lookup using the same request key, never a new execution.
        const recovered = await fetchChatRequestStatus(session.session_id, requestKey).catch(() => null);
        if (recovered?.trace_id) {
          setMessages(prev => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, agent_run_id: recovered.trace_id };
            return next;
          });
        }
        let content = `Request failed: ${err.message}`;
        if (recovered?.status === "completed" && typeof recovered.assistant_message === "string") {
          content = recovered.assistant_message;
          setLatestRunId(recovered.agent_run_id || null);
          setMessages(prev => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, agent_run_id: recovered.agent_run_id || null };
            return next;
          });
          setAgentStatus(isZh ? "已恢复完成结果" : "Completed result recovered");
          try {
            requestLedger.complete(requestIdentity, requestKey);
            setNotice(isZh ? "已从服务器恢复保存的回复，没有重复执行。" : "Recovered the saved response without executing again.");
          } catch {
            setNotice(isZh ? "回复已恢复，但本地请求标识未清除；再次发送仍会查询同一次请求。" : "Response recovered; the pending identity is retained locally.");
          }
          await refreshDashboard(session.user_id).catch(() => undefined);
        } else if (recovered?.status === "unconfirmed" || recovered?.status === "failed") {
          const writes = recovered.confirmed_writes.map((item) =>
            `${item.workout_name}${item.duration_minutes == null ? "" : ` ${item.duration_minutes} ${isZh ? "分钟" : "min"}`}`,
          );
          content = isZh
            ? `${writes.length ? `已确认保存：${writes.join("；")}。` : "目前没有可确认的写入结果，但不能据此认定没有写入。"}${recovered.status === "failed" ? "本轮执行发生错误。" : "本轮整体完成状态尚未确认。"}请勿更换消息重复记录。`
            : `${writes.length ? `Confirmed saved: ${writes.join("; ")}. ` : "No write is confirmed; this does not prove no write occurred. "}The overall request is ${recovered.status}. Do not submit a changed message to repeat the write.`;
          setAgentStatus(isZh ? "需要核对请求结果" : "Request requires reconciliation");
          setNotice(isZh ? "已保留原请求标识；本次只查询状态，没有重新执行。" : "Original request identity retained; status lookup did not execute again.");
        }
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") next[next.length - 1] = { ...last, content };
          return next;
        });
      } finally {
        sending.current = false;
        setBusy(false);
      }
    },
    [session, isZh],
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
        <span>{isZh ? "正在连接服务…" : "Loading..."}</span>
        <small>{isZh ? "正在确认账号与本地服务状态" : "Checking account and service status"}</small>
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
        <button className="sidebar-brand" type="button" aria-label={isZh ? "展开或收起导航" : "Toggle navigation"} aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((v) => !v)}>
          {sidebarOpen && <span className="fitagent-wordmark">Fit<span>Agent</span></span>}
          <PanelLeft size={21} />
        </button>

        <nav className="sidebar-nav">
          <NavItem icon={<MessageCircle size={20} />} label={isZh ? "对话" : "Chat"} active={activeView === "chat"} onClick={() => setActiveView("chat")} collapsed={!sidebarOpen} />
          <NavItem icon={<CalendarDays size={20} />} label={isZh ? "训练安排" : "Training schedule"} active={activeView === "plan"} onClick={() => setActiveView("plan")} collapsed={!sidebarOpen} />
          <NavItem icon={<ChartNoAxesColumn size={20} />} label={isZh ? "训练记录" : "Workout"} active={activeView === "workout"} onClick={() => setActiveView("workout")} collapsed={!sidebarOpen} />
          <NavItem icon={<TrendingUp size={20} />} label={isZh ? "长期跟踪" : "Follow-up"} active={activeView === "responsibilities"} onClick={() => setActiveView("responsibilities")} collapsed={!sidebarOpen} />
          {sidebarOpen && <SessionHistory session={session} busy={busy || sessionLoading} onSelect={selectSession} />}
        </nav>

        <div className="sidebar-footer">
          <NavItem icon={<Settings size={20} />} label={isZh ? "设置" : "Settings"} active={["settings", "account", "algorithm"].includes(activeView)} onClick={() => setActiveView("settings")} collapsed={!sidebarOpen} />
          <div className="sidebar-utilities">
            <button aria-label={isZh ? "今日概览" : "Today overview"} title={isZh ? "今日概览" : "Today overview"} onClick={() => setActiveView("dashboard")}><LayoutDashboard size={18} /></button>
            <button aria-label={isZh ? "状态打卡" : "Check-in"} title={isZh ? "状态打卡" : "Check-in"} onClick={() => setActiveView("checkin")}><ClipboardCheck size={18} /></button>
          <button className="sidebar-language" type="button" aria-label={isZh ? "切换显示语言" : "Switch interface language"} onClick={() => setLanguage(language === "zh" ? "en" : "zh")}>
            <Languages size={14} /> {sidebarOpen && (isZh ? "English" : "中文")}
          </button>
          </div>
          <div className="session-badge">
            <div className={`status-dot ${session ? "live" : "dead"}`} />
            {sidebarOpen && <span>{session ? (isZh ? "会话已连接" : "Session connected") : (isZh ? "会话连接中" : "Connecting session")}</span>}
          </div>
          {sidebarOpen && (
            <div className="sidebar-user">
              <button className="sidebar-user-profile" onClick={() => setActiveView("account")}>
                <span className="sidebar-avatar">
                  {auth.user.avatar_url ? (
                    <img src={auth.user.avatar_url} alt="" />
                  ) : (
                    <UserCircle size={25} />
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
        {usage && !usage.live_calls_available && (
          <div className="quota-banner">
            {isZh ? "当前不提供在线模型调用，使用离线规则回复。会话连接不代表模型已连接。" : "Live model calls are unavailable. Offline rules are active; a connected session is not a connected model."}
          </div>
        )}
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
            busy={busy || sessionLoading}
            session={session}
            agentStatus={sessionLoading ? (isZh ? "正在读取会话…" : "Loading conversation…") : agentStatus}
            agentTrace={agentTrace}
            latestRunId={latestRunId}
            profileComplete={profileComplete}
            onSend={sendMessage}
            dashboard={dashboard}
            onNavigate={setActiveView}
          />
        )}

        {activeView === "dashboard" && (
          <DashboardView
            dashboard={dashboard}
            session={session}
            busy={busy}
            onRefresh={() => session && refreshDashboard(session.user_id)}
            onNavigate={setActiveView}
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

        {(activeView === "workout" || activeView === "plan") && (
          <TrainingWorkspace
            initialPane={activeView === "plan" ? "adjustments" : "records"}
            dashboard={dashboard}
            key={auth.user.user_id}
            userId={auth.user.user_id}
            session={session}
            busy={busy}
            setBusy={setBusy}
            setNotice={setNotice}
            onRefresh={() => session && refreshDashboard(session.user_id)}
          />
        )}

        {activeView === "responsibilities" && <ResponsibilityView key={auth.user.user_id} userId={auth.user.user_id} />}
        {["settings", "account", "algorithm"].includes(activeView) && <SettingsView key={auth.user.user_id} initialSection={activeView === "account" ? "account" : activeView === "algorithm" ? "development" : "general"} />}
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
  const demo = useMemo(() => new URLSearchParams(window.location.search).get("demo") === "responsibilities" ? createResponsibilityDemo() : null, []);
  if (demo) return <LanguageProvider><div className="responsibility-demo-shell"><ResponsibilityView userId="synthetic-demo" gateway={demo.gateway} demoAdvance={demo.advance} /></div></LanguageProvider>;
  return (
    <LanguageProvider>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </LanguageProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
