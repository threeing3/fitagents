import { useState } from "react";
import { ChevronLeft, ChevronRight, CalendarDays, RefreshCw } from "lucide-react";
import { useLanguage } from "./LanguageContext";
import type { ApprovalActivity, Dashboard, TrainingExercise } from "./types";

export function accountDate(timezone = "Asia/Shanghai", now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const get = (name: string) => parts.find(part => part.type === name)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}
function shiftDate(date: string, days: number): string {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}
function prescription(exercise: TrainingExercise, isZh: boolean): string {
  if (exercise.duration_minutes != null) return `${exercise.duration_minutes} ${isZh ? "分钟" : "min"}`;
  if (exercise.sets != null && exercise.reps != null) return `${exercise.sets} × ${exercise.reps}`;
  if (exercise.sets != null) return `${exercise.sets} ${isZh ? "组" : "sets"}`;
  return isZh ? "未指定剂量" : "Not specified";
}

export function PlanSchedule({ dashboard, onRefresh }: { dashboard?: Dashboard | null; onRefresh?: () => void }) {
  const { isZh } = useLanguage();
  const [offset, setOffset] = useState(0);
  const today = accountDate(dashboard?.timezone);
  const weekday = new Date(`${today}T12:00:00Z`).getUTCDay();
  const start = shiftDate(today, -(weekday + 6) % 7 + offset * 7);
  const end = shiftDate(start, 6);
  const days = dashboard?.active_plan?.plan.training_days || [];
  const undated = days.filter(day => !day.date);
  return <section className="plan-schedule" aria-label={isZh ? "按日期保存的训练计划" : "Saved dated training plan"}>
    <header><div><p className="workspace-eyebrow">{isZh ? "已保存安排，不代表实际完成" : "Saved schedule, not completion"}</p><h3><CalendarDays size={19} />{isZh ? "训练计划" : "Training plan"}</h3><p>{start} — {end} · {dashboard?.timezone || "Asia/Shanghai"}</p></div>
      <div className="plan-week-controls"><button className="icon-btn" aria-label={isZh ? "上一周" : "Previous week"} onClick={() => setOffset(value => value - 1)}><ChevronLeft size={18} /></button><button className="pill-btn" onClick={() => setOffset(0)}>{isZh ? "回到本周" : "This week"}</button><button className="icon-btn" aria-label={isZh ? "下一周" : "Next week"} onClick={() => setOffset(value => value + 1)}><ChevronRight size={18} /></button>{onRefresh && <button className="icon-btn" aria-label={isZh ? "刷新已保存计划" : "Refresh saved plan"} onClick={onRefresh}><RefreshCw size={17} /></button>}</div>
    </header>
    {!dashboard ? <p role="status">{isZh ? "计划暂未读取，请刷新概览核对。" : "Plan unavailable. Refresh dashboard."}</p> : <>
      <div className="plan-table-wrap"><table><caption className="sr-only">{isZh ? "当前周的已保存训练安排" : "Saved sessions this week"}</caption><thead><tr><th>{isZh ? "日期" : "Date"}</th><th>{isZh ? "训练安排" : "Session"}</th><th>{isZh ? "计划状态" : "Plan state"}</th></tr></thead><tbody>{Array.from({ length: 7 }, (_, index) => {
        const date = shiftDate(start, index);
        const matches = days.filter(day => day.date === date);
        return <tr key={date} className={date === today ? "plan-today" : ""}><th scope="row">{date.slice(5)}{date === today && <small>{isZh ? "今天" : "Today"}</small>}</th><td>{matches.length === 1 ? <details><summary>{matches[0].name || (isZh ? "训练安排" : "Session")}</summary>{(matches[0].exercises || []).map((ex, i) => <p key={i}>{ex.name} · {prescription(ex, isZh)}</p>)}</details> : matches.length > 1 ? (isZh ? "存在多份同日安排，请先核对" : "Multiple sessions. Clarify first.") : (isZh ? "暂无已保存安排" : "No saved session")}</td><td>{matches.length === 1 ? (isZh ? "已保存 · 未核对完成" : "Saved · completion unverified") : matches.length > 1 ? (isZh ? "日期有歧义" : "Ambiguous date") : "—"}</td></tr>;
      })}</tbody></table></div>
      {undated.length > 0 && <details className="undated-plan"><summary>{isZh ? `另有 ${undated.length} 个未指定日期的安排` : `${undated.length} undated sessions`}</summary><p>{isZh ? "不把旧的第几天自动映射到日历。请在对话中确认日期。" : "Ordinal days are not mapped to calendar dates. Confirm dates in chat."}</p>{undated.map((day, index) => <p key={index}>{day.name || `${isZh ? "安排" : "Session"} ${index + 1}`}</p>)}</details>}
    </>}
  </section>;
}

export function ProposalDiff({ item }: { item: ApprovalActivity }) {
  const { isZh } = useLanguage();
  const day = item.input_preview.day_date;
  const before = item.context.baseline_plan?.training_days?.filter(row => row.date === day) || [];
  const after = item.context.candidate_plan?.training_days?.filter(row => row.date === day) || [];
  const available = before.length === 1 && after.length === 1 && !!before[0].exercises?.length && before[0].exercises.length === after[0].exercises?.length;
  if (!available) return <p className="proposal-diff-unavailable">{isZh ? "该草案缺少唯一的原计划与候选快照，不能展示准确的逐动作差异。请先核对依据。" : "Exact before/after snapshots unavailable. Inspect evidence first."}</p>;
  return <div className="proposal-diff"><p>{isZh ? "候选调整 · 尚未修改计划" : "Proposed changes · plan unchanged"}</p><div className="plan-table-wrap"><table><caption className="sr-only">{isZh ? "指定日期训练调整前后对比" : "Before and proposed changes"}</caption><thead><tr><th>{isZh ? "日期" : "Date"}</th><th>{isZh ? "动作" : "Exercise"}</th><th>{isZh ? "原计划" : "Before"}</th><th>{isZh ? "候选调整" : "Proposed"}</th></tr></thead><tbody>{before[0].exercises!.map((exercise, index) => <tr key={index}>{index === 0 && <th scope="rowgroup" rowSpan={before[0].exercises!.length}>{day}</th>}<td>{exercise.name}{exercise.name !== after[0].exercises![index].name && <small>{isZh ? "候选动作：" : "Proposed: "}{after[0].exercises![index].name}</small>}</td><td>{prescription(exercise, isZh)}</td><td>{prescription(after[0].exercises![index], isZh)}</td></tr>)}</tbody></table></div><small>{isZh ? "批准后仍需后台复核并执行；此表不是已完成结果。" : "Approval still requires worker validation and execution."}</small></div>;
}
