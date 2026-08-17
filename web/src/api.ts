import type { AgentChallengeSummary, AgentRunAnalysis, AgentRunDetail, AlgorithmCompare, AlgorithmSummary, AuthUser, ChatMessage, CheckinResult, Dashboard, PlanResponse, SessionState, UsageSummary } from "./types";

const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? "http://127.0.0.1:1015"
  : window.location.origin;
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL).replace(/\/$/, "");
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text || response.statusText;
    try {
      const parsed = JSON.parse(text);
      detail = String(parsed.detail || parsed.message || parsed.error?.message || detail);
    } catch {
      // Keep the non-JSON upstream message.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
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

export async function fetchUsageSummary(): Promise<UsageSummary> {
  return api("/v1/usage/summary");
}

export async function fetchAlgorithmSummary(): Promise<AlgorithmSummary> {
  return api("/v1/algorithm/summary");
}

export async function fetchAgentLabRuns(): Promise<AgentRunAnalysis[]> {
  return api("/v1/algorithm/agent-runs?limit=20");
}

export async function fetchAgentLabRun(runId: string): Promise<AgentRunAnalysis> {
  return api(`/v1/algorithm/agent-runs/${runId}`);
}

export async function fetchAgentChallenges(): Promise<AgentChallengeSummary> {
  return api("/v1/algorithm/challenges/summary");
}

export async function compareIntent(message: string): Promise<AlgorithmCompare> {
  return api("/v1/algorithm/compare", {
    method: "POST",
    body: JSON.stringify({ message }),
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
  return fetch(`${API_BASE_URL}/v1/chat/messages/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, message }),
  });
}
