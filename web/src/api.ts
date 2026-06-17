import type { AuthUser, ChatMessage, Dashboard, PlanResponse, CheckinResult, SessionState, AgentRunDetail } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:1015").replace(/\/$/, "");
const TOKEN_KEY = "ai_fitness_token";

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const authHeaders = getAuthHeaders();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders, ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    // Token expired or invalid — clear auth and reload to show login
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem("ai_fitness_user");
    window.location.reload();
    throw new Error("Session expired. Please sign in again.");
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

export function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---- high-level API helpers ----

export async function createSession(displayName: string = "Fitness User"): Promise<SessionState & { title: string; created_at: string }> {
  return api("/v1/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ display_name: displayName, title: "AI Coach Session" }),
  });
}

export async function updateAccount(payload: {
  display_name?: string;
  username?: string;
  avatar_url?: string;
}): Promise<AuthUser> {
  return api("/v1/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function listSessions(): Promise<Array<SessionState & { title: string; created_at: string }>> {
  return api("/v1/chat/sessions");
}

export async function fetchSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const rows = await api<Array<ChatMessage & { session_id: string; user_id: string }>>(
    `/v1/chat/sessions/${sessionId}/messages?limit=500`,
  );
  return rows
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      created_at: message.created_at,
    }));
}

export async function fetchDashboard(userId: string): Promise<Dashboard> {
  return api(`/v1/users/${userId}/dashboard`);
}

export async function fetchAgentRun(runId: string): Promise<AgentRunDetail> {
  return api(`/v1/agent-runs/${runId}`);
}

export async function generatePlan(userId: string): Promise<PlanResponse> {
  return api("/v1/plans/generate", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, force: true, plan_days: 7 }),
  });
}

export async function submitCheckin(
  userId: string,
  data: Record<string, any>,
): Promise<CheckinResult> {
  return api("/v1/checkins/daily", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, ...data }),
  });
}

export async function logWorkout(
  userId: string,
  data: Record<string, any>,
): Promise<{ status: string; workout_log_id: string }> {
  return api("/v1/workouts/logs", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, ...data }),
  });
}

export function streamChat(
  sessionId: string,
  userId: string,
  message: string,
): Promise<Response> {
  const authHeaders = getAuthHeaders();
  return fetch(`${API_BASE_URL}/v1/chat/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, message }),
  });
}
