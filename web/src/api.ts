import type { Dashboard, PlanResponse, CheckinResult, SessionState } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:1015").replace(/\/$/, "");
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

export async function fetchDashboard(userId: string): Promise<Dashboard> {
  return api(`/v1/users/${userId}/dashboard`);
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
