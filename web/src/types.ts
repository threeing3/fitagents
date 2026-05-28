// ---- API response types (mirrors backend schemas) ----

export type SessionState = {
  session_id: string;
  user_id: string;
};

export type Dashboard = {
  profile_complete: boolean;
  profile: Record<string, any>;
  missing_slots: string[];
  today_plan: Record<string, any>;
  latest_checkin: Record<string, any> | null;
  recent_memories: Array<Record<string, any>>;
  progress: Record<string, any>;
  coach_suggestions: string[];
};

export type AgentTraceItem = {
  id: string;
  type: "status" | "step" | "tool_call" | "error" | "done";
  title: string;
  summary: string;
  latency_ms?: number;
  metadata?: Record<string, any>;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type PlanResponse = {
  plan_id: string;
  status: string;
  plan: Record<string, any>;
  rationale: string;
};

export type CheckinResult = {
  status: string;
  checkin_id: string;
  auto_adjusted: boolean;
};

// ---- UI view state ----

export type ViewName = "chat" | "dashboard" | "checkin" | "workout";
