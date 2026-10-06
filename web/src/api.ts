import type { AgentChallengeSummary, AgentRunAnalysis, AgentRunDetail, AlgorithmCompare, AlgorithmSummary, AuthUser, ChatMessage, CheckinResult, Dashboard, IntentEvaluationSummary, PlanResponse, SessionState, UsageSummary } from "./types";

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
    throw Object.assign(new Error(detail), { status: response.status });
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---- high-level API helpers ----

export const responsibilityApi = {
  reconcile: (id: string, expectedAttempt: number) => api<{ status: string; changed: boolean; requeued: boolean }>(`/v1/tasks/${encodeURIComponent(id)}/reconcile`, { method: "POST", body: JSON.stringify({ expected_attempt: expectedAttempt, confirm_no_retry: true }) }),
  list: () => api<import("./types").Responsibility[]>("/v1/responsibilities"),
  history: () => api<import("./types").ApprovalActivity[]>("/v1/approvals/history?limit=30"),
  create: (payload: import("./types").WeeklyResponsibilityInput) => api<import("./types").Responsibility>("/v1/responsibilities/weekly-review", { method: "POST", body: JSON.stringify(payload) }),
  lifecycle: (id: string, action: "pause" | "resume" | "cancel") => api<import("./types").Responsibility>(`/v1/responsibilities/${encodeURIComponent(id)}/lifecycle`, { method: "POST", body: JSON.stringify({ action }) }),
  decide: (id: string, action: "approve" | "deny") => api<{ status: string }>("/v1/approvals/decide", { method: "POST", body: JSON.stringify({ approval_id: id, action }) }),
};

export type DecisionFollowup = {
  id: string;
  evaluation_plan_id: string;
  status: string;
  question: { question?: string; text?: string; [key: string]: unknown };
};

export const followupApi = {
  list: () => api<DecisionFollowup[]>("/v1/agent/decision-followups"),
  decline: (id: string) => api<DecisionFollowup>(`/v1/agent/decision-followups/${encodeURIComponent(id)}/decline`, { method: "POST" }),
};

export type WorkoutFacts = {
  duration_minutes: number | null;
  rpe: number | null;
  completion_rate: number | null;
};
export type SavedWorkout = WorkoutFacts & {
  id: string;
  performed_at: string;
  workout_name: string;
  revision: number | null;
  correction_available: boolean;
};
export type WorkoutCorrection = {
  idempotency_key: string;
  expected_revision: number;
  expected: Partial<WorkoutFacts>;
  changes: Partial<WorkoutFacts>;
  reason: string;
};
export const workoutCorrectionApi = {
  list: () => api<SavedWorkout[]>("/v1/workouts/logs?limit=30"),
  correct: (id: string, payload: WorkoutCorrection) => api<{
    status: string; revision: number; audit_id: string; idempotent_replay: boolean;
    before: Partial<WorkoutFacts>; after: Partial<WorkoutFacts>;
  }>(`/v1/workouts/logs/${encodeURIComponent(id)}/corrections`, {
    method: "POST", body: JSON.stringify(payload), signal: AbortSignal.timeout(20000),
  }),
};

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

export async function fetchIntentEvaluation(): Promise<IntentEvaluationSummary> {
  return api("/v1/algorithm/intent-evaluation/summary");
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
      execution_events: message.execution_events,
      agent_run_id: message.agent_run_id,
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
  idempotencyKey: string,
): Promise<CheckinResult> {
  return api("/v1/checkins/daily", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ user_id: userId, ...data }),
  });
}

export type SubagentCatalog = {
  parent_id: string; revision: number; recorded_at: string;
  execution_lease?: { protocol: number } | null;
  stop_control?: { protocol: number } | null;
  continuation_available?: boolean;
  children: Array<{ child_id: string; domain: string; status: string; failure_reason?: string }>;
};

export type SubagentTurnResult = {
  status: string; no_business_writes: boolean;
  failure_reason?: string;
  advice?: { summary?: string; recommendations?: string[]; uncertainties?: string[] };
};
export function requestSubagentTurn(sessionId: string, key: string, message: string, role: string, row?: SubagentCatalog) {
  const base = `/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagents`;
  return api<SubagentTurnResult>(row ? `${base}/${encodeURIComponent(row.parent_id)}/continue` : base, {
    method: "POST", headers: { "Idempotency-Key": key },
    body: JSON.stringify(row ? { message, expected_catalog_revision: row.revision } : { message, role }),
  });
}
export function fetchSubagentRequestStatus(sessionId: string, key: string) {
  return api<{ status: string; result?: SubagentTurnResult }>(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagent-requests/status`,
    { headers: { "Idempotency-Key": key } },
  );
}

export function cancelSubagentRequest(sessionId: string, key: string) {
  return api<{ status: string; no_automatic_retry: boolean }>(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagent-requests/cancel`,
    { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ confirm_no_retry: true }) },
  );
}

export function reconcileSubagentRequest(sessionId: string, key: string, revision: number) {
  return api<{ status: string; result?: SubagentTurnResult }>(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagent-requests/reconcile`,
    { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ expected_revision: revision, confirm_no_retry: true }) },
  );
}

export function reconcileSubagentCatalog(sessionId: string, row: SubagentCatalog) {
  return api<{ changed: boolean; requeued: boolean; revision: number }>(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(row.parent_id)}/reconcile`,
    { method: "POST", body: JSON.stringify({ expected_revision: row.revision, confirm_no_retry: true }) },
  );
}

export function stopSubagentTree(sessionId: string, row: SubagentCatalog) {
  return api<{ status: string }>(`/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(row.parent_id)}/stop`,
    { method: "POST", body: JSON.stringify({ expected_revision: row.revision, confirm_no_retry: true }) });
}

export async function fetchSubagentCatalogs(sessionId: string, signal?: AbortSignal): Promise<SubagentCatalog[]> {
  const rows = await api<SubagentCatalog[]>(`/v1/chat/sessions/${encodeURIComponent(sessionId)}/subagents?limit=10`, { signal });
  if (!Array.isArray(rows) || rows.some(row => !row || typeof row.parent_id !== "string"
    || !Number.isInteger(row.revision) || typeof row.recorded_at !== "string"
    || !Array.isArray(row.children) || row.children.some(child => !child
      || typeof child.child_id !== "string" || typeof child.domain !== "string" || typeof child.status !== "string"))) {
    throw new Error("Invalid child catalog response");
  }
  return rows;
}

export async function logWorkout(
  userId: string,
  data: Record<string, any>,
  idempotencyKey: string,
): Promise<{ status: string; workout_log_id: string; idempotent_replay: boolean }> {
  return api("/v1/workouts/logs", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ user_id: userId, ...data }),
  });
}

export type ChatRequestStatus = {
  status: "not_found" | "unconfirmed" | "failed" | "completed";
  confirmed_writes: { kind: string; record_id: string; workout_name: string; duration_minutes: number | null }[];
  assistant_message?: string;
  agent_run_id?: string;
  trace_id?: string;
  trace_state?: string;
  may_repeat_writes: boolean;
};

export function fetchChatRequestStatus(sessionId: string, key: string): Promise<ChatRequestStatus> {
  return api(`/v1/chat/requests/status?session_id=${encodeURIComponent(sessionId)}`, {
    headers: { "Idempotency-Key": key },
    signal: AbortSignal.timeout(10000),
  });
}

export function streamChat(
  sessionId: string,
  userId: string,
  message: string,
  idempotencyKey: string,
): Promise<Response> {
  return fetch(`${API_BASE_URL}/v1/chat/messages/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, message, idempotency_key: idempotencyKey }),
  });
}
