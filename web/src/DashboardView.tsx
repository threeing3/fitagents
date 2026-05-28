import React, { useMemo } from "react";
import {
  Activity, Moon, Utensils, Dumbbell, Target, RefreshCw,
  TrendingUp, Battery, Gauge, Flame, Apple, Heart,
} from "lucide-react";
import type { SessionState, Dashboard } from "./types";
import { generatePlan } from "./api";

type Props = {
  dashboard: Dashboard | null;
  session: SessionState | null;
  busy: boolean;
  onRefresh: () => void;
};

export function DashboardView({ dashboard, session, busy, onRefresh }: Props) {
  const profile = dashboard?.profile;
  const checkin = dashboard?.latest_checkin;
  const todayPlan = dashboard?.today_plan;

  // Compute a simple readiness score (0-100)
  const readiness = useMemo(() => {
    if (!checkin) return null;
    let score = 70;
    if (checkin.sleep_hours) score += (checkin.sleep_hours - 7) * 8;
    if (checkin.fatigue) score -= (checkin.fatigue - 3) * 5;
    if (checkin.soreness) score -= (checkin.soreness - 3) * 5;
    if (checkin.stress) score -= (checkin.stress - 3) * 4;
    return Math.max(10, Math.min(100, Math.round(score)));
  }, [checkin]);

  const readinessColor = !readiness ? "#3a3a4a" : readiness >= 70 ? "#22c55e" : readiness >= 40 ? "#f59e0b" : "#ef4444";

  async function handleGeneratePlan() {
    if (!session) return;
    try {
      await generatePlan(session.user_id);
      onRefresh();
    } catch {}
  }

  return (
    <div className="dashboard-view">
      <div className="dash-header">
        <h2>Dashboard</h2>
        <button className="icon-btn" onClick={onRefresh} disabled={busy}>
          <RefreshCw size={18} className={busy ? "spin" : ""} />
        </button>
      </div>

      <div className="dash-grid">
        {/* ---- Readiness Gauge ---- */}
        <div className="dash-card readiness-card">
          <div className="card-label">Readiness</div>
          <div className="readiness-gauge" style={{ borderColor: readinessColor }}>
            <div className="gauge-inner">
              <span className="gauge-value" style={{ color: readinessColor }}>{readiness ?? "--"}</span>
              <span className="gauge-label">/100</span>
            </div>
            <svg className="gauge-ring" viewBox="0 0 120 120">
              <circle cx="60" cy="60" r="52" fill="none" stroke="#2a2a3a" strokeWidth="8" />
              {readiness != null && (
                <circle
                  cx="60" cy="60" r="52" fill="none"
                  stroke={readinessColor} strokeWidth="8"
                  strokeLinecap="round"
                  strokeDasharray={`${(readiness / 100) * 327} 327`}
                  transform="rotate(-90 60 60)"
                  style={{ transition: "stroke-dasharray 0.8s ease" }}
                />
              )}
            </svg>
          </div>
          <div className="readiness-breakdown">
            <MiniStat icon={<Moon size={14} />} label="Sleep" value={checkin?.sleep_hours ? `${checkin.sleep_hours}h` : "--"} />
            <MiniStat icon={<Battery size={14} />} label="Fatigue" value={checkin?.fatigue != null ? `${checkin.fatigue}/10` : "--"} />
            <MiniStat icon={<Gauge size={14} />} label="Soreness" value={checkin?.soreness != null ? `${checkin.soreness}/10` : "--"} />
          </div>
        </div>

        {/* ---- Metrics cards ---- */}
        <MetricCard
          icon={<Flame size={22} />}
          label="Nutrition Target"
          value={profile?.target_calories ? `${profile.target_calories}` : "--"}
          unit="kcal"
          detail={`Protein ${profile?.target_protein_g ? Math.round(profile.target_protein_g) + "g" : "--"}`}
          accent="#f59e0b"
        />
        <MetricCard
          icon={<Dumbbell size={22} />}
          label="Workouts Logged"
          value={`${dashboard?.progress?.workouts_logged ?? "--"}`}
          unit="sessions"
          detail={dashboard?.progress?.active_plan ? "Active plan" : "No plan yet"}
          accent="#3b82f6"
        />
        <MetricCard
          icon={<Heart size={22} />}
          label="Profile"
          value={dashboard?.profile_complete ? "Complete" : "Building"}
          unit=""
          detail={dashboard?.missing_slots?.length ? `Missing: ${dashboard.missing_slots.join(", ")}` : "All fields set"}
          accent={dashboard?.profile_complete ? "#22c55e" : "#f59e0b"}
        />

        {/* ---- Today's Workout ---- */}
        <div className="dash-card wide">
          <div className="card-label">Today's Workout</div>
          {todayPlan && todayPlan.name ? (
            <div className="workout-card-content">
              <div className="workout-card-header">
                <Activity size={20} />
                <div>
                  <strong>{todayPlan.name}</strong>
                  <span>{todayPlan.focus}</span>
                </div>
              </div>
              <div className="exercise-rows">
                {(todayPlan.exercises || []).slice(0, 6).map((ex: any, i: number) => (
                  <div key={i} className="exercise-row">
                    <span className="ex-name">{ex.name}</span>
                    <span className="ex-prescription">{ex.sets} × {ex.reps}</span>
                    <span className="ex-rest">{ex.rest_seconds}s rest</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="empty-card">
              <Dumbbell size={32} />
              <p>Complete your profile to see today's workout plan.</p>
              <button className="pill-btn" onClick={handleGeneratePlan} disabled={busy || !session}>
                <Target size={14} />
                Generate Plan
              </button>
            </div>
          )}
        </div>

        {/* ---- Coach Suggestions ---- */}
        <div className="dash-card">
          <div className="card-label">Coach Suggestions</div>
          {dashboard?.coach_suggestions?.length ? (
            <ul className="suggestion-list">
              {dashboard.coach_suggestions.map((s, i) => (
                <li key={i}><TrendingUp size={14} />{s}</li>
              ))}
            </ul>
          ) : (
            <div className="empty-card small">
              <p>Chat with your coach to get personalized suggestions.</p>
            </div>
          )}
        </div>

        {/* ---- Recent Memories ---- */}
        <div className="dash-card wide">
          <div className="card-label">Recent Memories</div>
          {dashboard?.recent_memories?.length ? (
            <div className="memory-list">
              {dashboard.recent_memories.slice(0, 6).map((m: any, i: number) => (
                <div key={i} className="memory-item">
                  <span className={`memory-badge ${m.memory_type || ""}`}>{m.memory_type || "note"}</span>
                  <p>{m.content || m.summary}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-card small"><p>No memories yet. Chat with your coach to build long-term memory.</p></div>
          )}
        </div>
      </div>
    </div>
  );
}

function MetricCard({ icon, label, value, unit, detail, accent }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  detail: string;
  accent: string;
}) {
  return (
    <div className="dash-card metric-card-dash">
      <div className="card-label">{label}</div>
      <div className="metric-icon-dash" style={{ color: accent }}>{icon}</div>
      <div className="metric-value-dash">
        <span className="metric-big">{value}</span>
        {unit && <span className="metric-unit">{unit}</span>}
      </div>
      <p className="metric-detail">{detail}</p>
    </div>
  );
}

function MiniStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="mini-stat">
      {icon}
      <span className="mini-label">{label}</span>
      <span className="mini-value">{value}</span>
    </div>
  );
}
