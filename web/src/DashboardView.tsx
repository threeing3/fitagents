import React from "react";
import {
  Activity, Moon, Utensils, Dumbbell, Target, RefreshCw,
  TrendingUp, Battery, Gauge, Flame, Apple, Heart,
} from "lucide-react";
import type { SessionState, Dashboard, ViewName } from "./types";
import { generatePlan } from "./api";
import { useLanguage } from "./LanguageContext";
import { goalLabel } from "./goalLabel";
import { PendingProposalPreview } from "./PendingProposalPreview";
import { accountDate } from "./PlanReview";

type Props = {
  dashboard: Dashboard | null;
  session: SessionState | null;
  busy: boolean;
  onRefresh: () => void;
  onNavigate?: (view: ViewName) => void;
};

export function DashboardView({ dashboard, session, busy, onRefresh, onNavigate }: Props) {
  const { isZh } = useLanguage();
  const profile = dashboard?.profile;
  const checkin = dashboard?.latest_checkin;
  const todayPlan = dashboard?.today_plan;

  const today = new Date(`${accountDate(dashboard?.timezone)}T12:00:00Z`);
  const monday = new Date(today);
  monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
  const week = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday);
    date.setUTCDate(monday.getUTCDate() + index);
    return date;
  });

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
        <div><p className="workspace-eyebrow">{isZh ? "你的训练工作台" : "Your training workspace"}</p><h2>{isZh ? "今天，按自己的节奏来" : "Today, at your own pace"}</h2></div>
        <button className="icon-btn" aria-label={isZh ? "刷新概览" : "Refresh dashboard"} onClick={onRefresh} disabled={busy}>
          <RefreshCw size={18} className={busy ? "spin" : ""} />
        </button>
      </div>

      <div className="training-week" aria-label={isZh ? "本周日期，仅日历，不表示训练完成" : "Current week calendar, not workout completion"}>
        {week.map(date => <div key={date.toISOString()} aria-current={date.toISOString() === today.toISOString() ? "date" : undefined}>
          <span>{date.toLocaleDateString(isZh ? "zh-CN" : "en-US", { weekday: "short", timeZone: "UTC" })}</span>
          <strong>{date.toLocaleDateString(isZh ? "zh-CN" : "en-US", { month: "numeric", day: "numeric", timeZone: "UTC" })}</strong>
        </div>)}
      </div>
      {onNavigate && <section className="today-action" aria-label={isZh ? "今天的下一步" : "Today's next step"}>
        <div><p className="workspace-eyebrow">{isZh ? "下一步，由你选择" : "Choose your next step"}</p>
          <h3>{todayPlan?.name ? (isZh ? "记录实际完成的训练" : "Record what you actually did") : (isZh ? "先确定今天的安排" : "Choose today's plan")}</h3>
          <p>{isZh ? "你可以继续当前计划，也可以说明新的日期、运动类型和限制。调整先审阅，再决定是否批准。" : "Keep your plan or specify a date, activity and constraints. Review changes before approving them."}</p>
          <button className="pill-btn" onClick={() => onNavigate(todayPlan?.name ? "workout" : "chat")}>{todayPlan?.name ? (isZh ? "记录训练" : "Record workout") : (isZh ? "说明训练需求" : "Describe training needs")}</button>
          <PendingProposalPreview userId={session?.user_id} onReview={() => onNavigate("responsibilities")} />
        </div>
        <aside><Target size={20} /><h3>{isZh ? "目标与调整" : "Goals and changes"}</h3>
          <p>{goalLabel(profile?.goal, isZh)}</p>
          <button onClick={() => onNavigate("responsibilities")}>{isZh ? "审阅待批准调整与长期跟踪" : "Review changes and ongoing goals"}</button>
        </aside>
      </section>}

      <div className="dash-grid">
        {/* ---- Readiness Gauge ---- */}
        <div className="dash-card readiness-card">
          <div className="card-label">{isZh ? "最近一次状态记录" : "Latest reported state"}</div>
          <p className="reported-state-note">{isZh ? "保留你记录的感受，不换算成未经验证的准备度评分。" : "Your reported feedback, not an unvalidated readiness score."}</p>
          <div className="readiness-breakdown">
            <MiniStat icon={<Moon size={14} />} label={isZh ? "睡眠" : "Sleep"} value={checkin?.sleep_hours != null ? `${checkin.sleep_hours}h` : "--"} />
            <MiniStat icon={<Battery size={14} />} label={isZh ? "疲劳" : "Fatigue"} value={checkin?.fatigue != null ? `${checkin.fatigue}/10` : "--"} />
            <MiniStat icon={<Gauge size={14} />} label={isZh ? "酸痛" : "Soreness"} value={checkin?.soreness != null ? `${checkin.soreness}/10` : "--"} />
          </div>
        </div>

        {/* ---- Metrics cards ---- */}
        <MetricCard
          icon={<Flame size={22} />}
          label={isZh ? "营养目标" : "Nutrition Target"}
          value={profile?.target_calories ? `${profile.target_calories}` : "--"}
          unit="kcal"
          detail={`${isZh ? "蛋白质" : "Protein"} ${profile?.target_protein_g ? Math.round(profile.target_protein_g) + "g" : "--"}`}
          accent="#f59e0b"
        />
        <MetricCard
          icon={<Dumbbell size={22} />}
          label={isZh ? "已记录训练" : "Workouts Logged"}
          value={`${dashboard?.progress?.workouts_logged ?? "--"}`}
          unit={isZh ? "次" : "sessions"}
          detail={dashboard?.progress?.active_plan ? (isZh ? "计划执行中" : "Active plan") : (isZh ? "暂无计划" : "No plan yet")}
          accent="#3b82f6"
        />
        <MetricCard
          icon={<Heart size={22} />}
          label={isZh ? "用户档案" : "Profile"}
          value={dashboard?.profile_complete ? (isZh ? "完整" : "Complete") : (isZh ? "建立中" : "Building")}
          unit=""
          detail={dashboard?.missing_slots?.length ? `${isZh ? "待补充" : "Missing"}: ${dashboard.missing_slots.join(", ")}` : (isZh ? "字段已齐全" : "All fields set")}
          accent={dashboard?.profile_complete ? "#22c55e" : "#f59e0b"}
        />

        {/* ---- Today's Workout ---- */}
        <div className="dash-card wide">
          <div className="card-label">{isZh ? "今日训练" : "Today's Workout"}</div>
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
                    <span className="ex-prescription">{ex.duration_minutes != null
                      ? (isZh ? `${ex.duration_minutes} 分钟` : `${ex.duration_minutes} min`)
                      : `${ex.sets} × ${ex.reps}`}</span>
                    <span className="ex-rest">{ex.duration_minutes != null
                      ? (isZh ? "按该次训练提示进行" : "Follow session guidance")
                      : (isZh ? `休息 ${ex.rest_seconds} 秒` : `${ex.rest_seconds}s rest`)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="empty-card">
              <Dumbbell size={32} />
              <p>{dashboard?.profile_complete
                ? (isZh ? "今天暂无训练安排；其他日期的已保存计划仍保留。需要新增时，可在聊天中指定日期。" : "No workout scheduled today. Saved sessions on other dates remain; request a date in chat to add one.")
                : (isZh ? "完善档案后可生成今日训练计划。" : "Complete your profile to see today's workout plan.")}</p>
              <button className="pill-btn" onClick={onNavigate ? () => onNavigate("chat") : handleGeneratePlan} disabled={busy || (!onNavigate && !session)}>
                <Target size={14} />
                {onNavigate ? (isZh ? "在对话中说明安排" : "Describe a plan in chat") : (isZh ? "生成计划" : "Generate Plan")}
              </button>
            </div>
          )}
        </div>

        {/* ---- Coach Suggestions ---- */}
        <div className="dash-card">
          <div className="card-label">{isZh ? "可供参考的建议" : "Options to consider"}</div>
          {dashboard?.coach_suggestions?.length ? (
            <ul className="suggestion-list">
              {dashboard.coach_suggestions.map((s, i) => (
                <li key={i}><TrendingUp size={14} />{s}</li>
              ))}
            </ul>
          ) : (
            <div className="empty-card small">
              <p>{isZh ? "说明你的需求后，再一起比较可选方案。" : "Describe your needs to compare available options."}</p>
            </div>
          )}
        </div>

        {/* ---- Recent Memories ---- */}
        <div className="dash-card wide">
          <div className="card-label">{isZh ? "近期记忆" : "Recent Memories"}</div>
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
            <div className="empty-card small"><p>{isZh ? "暂无记忆；通过对话逐步建立长期记忆。" : "No memories yet. Build your context through conversation."}</p></div>
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
