import type { ApprovalActivity, Responsibility, ExecutionEvent } from "./types";
import type { ResponsibilityGateway } from "./ResponsibilityView";

export function createResponsibilityDemo(): { gateway: ResponsibilityGateway; advance: () => void } {
  const now = new Date();
  const journal = (name: string, status: ExecutionEvent["status"], summary: string, source: ExecutionEvent["source"], details = {}): ExecutionEvent => ({ type: "execution_event", name, status, summary: `【界面模拟】${summary}`, source, recorded_at: new Date().toISOString(), details });
  const reviewEvents = [
    journal("review.evidence", "completed", "读取3个合成恢复记录。", "runtime", { evidence_ids: ["synthetic-1", "synthetic-2", "synthetic-3"] }),
    journal("review.decision", "completed", "演示规则判断疲劳均值为8，不是模型推理或临床结论。", "rule", { average_fatigue: 8, policy: "demo_fatigue_v1_not_clinical" }),
    journal("review.proposal", "blocked", "生成下一次训练减1组的草案，尚未修改计划。", "runtime"),
  ];
  function nextWake(weekday: number, hour: number, minute: number): string {
    const local = new Date(now.getTime()+8*3600000);
    const days = ((weekday+1)%7-local.getUTCDay()+7)%7;
    let candidate = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()+days, hour-8, minute);
    if (candidate <= now.getTime()) candidate += 7*86400000;
    return new Date(candidate).toISOString();
  }
  const next = nextWake(6, 18, 0);
  const expires = new Date(now.getTime()+86400000).toISOString();
  const trainingDate = new Date(now.getTime()+8*3600000+86400000).toISOString().slice(0,10);
  const end = new Date(now.getTime() + 28 * 86400000).toISOString();
  const tasks: Responsibility[] = [{
    id: "demo-review", status: "active", objective: "合成数据每周复盘",
    configuration: { timezone: "Asia/Shanghai", starts_at: now.toISOString(), ends_at: end,
      next_wake_at: next, weekday: 6, hour: 18, minute: 0, source_instruction: "四周训练跟踪，每周日18:00复盘；修改计划先问我" },
    progress: { reviews_completed: 1, last_review: {
      execution_events: reviewEvents,
      scheduled_at: now.toISOString(), week_start: new Date(now.getTime()-7*86400000).toISOString().slice(0,10), week_end: now.toISOString().slice(0,10),
      memories: [{ id: "demo-memory", summary: "合成恢复记录：三个已结束日期的疲劳评分为8、7、9。仅用于交互演示。", evidence: [{ table: "recovery_logs", id: "synthetic-1" }] }],
      adjustment_proposal: { status: "waiting_approval", approval_id: "demo-approval" },
    } },
  }];
  const activities: ApprovalActivity[] = [{
    approval_id: "demo-approval", tool_name: "plan.reduce_sets", tool_description: "仅减少指定日期的训练组数",
    status: "pending", created_at: now.toISOString(), expires_at: expires,
    input_preview: { plan_id: "demo-plan", day_date: trainingDate, reduce_by: 1,
      reason: "合成证据的演示规则：恢复疲劳均值为8，建议下一次各动作减1组。非临床结论。" },
    context: { baseline_plan: { training_days: [{ date: trainingDate, name: "合成全身力量", exercises: [{ name: "深蹲", sets: 3, reps: 10 }, { name: "划船", sets: 3, reps: 10 }] }] }, candidate_plan: { training_days: [{ date: trainingDate, name: "合成全身力量", exercises: [{ name: "深蹲", sets: 2, reps: 10 }, { name: "划船", sets: 2, reps: 10 }] }] }, execution_events: [...reviewEvents, journal("approval.wait", "blocked", "等待用户审批。", "runtime")], responsibility_id: "demo-review", review_signal: { average_fatigue: 8,
      evidence: [1,2,3].map(index => ({ id: `synthetic-${index}`, table: "recovery_logs" })) } },
    job_id: "demo-job", job_status: "waiting_approval", result: {}, error: null,
  }];
  const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
  const gateway: ResponsibilityGateway = {
    list: async () => clone(tasks), history: async () => clone(activities),
    create: async input => {
      const task: Responsibility = { id: `demo-${crypto.randomUUID()}`, status: "active", objective: "合成演示责任",
        configuration: { ...tasks[0].configuration, ...input, next_wake_at: nextWake(input.weekday, input.hour, input.minute), ends_at: new Date(now.getTime()+input.weeks*7*86400000).toISOString() },
        progress: { reviews_completed: 0 } };
      tasks.unshift(task); return clone(task);
    },
    lifecycle: async (id, action) => {
      const task = tasks.find(item => item.id === id);
      if (!task || !["active", "paused"].includes(task.status)) throw new Error("演示责任不存在或已终止");
      task.status = { pause: "paused", resume: "active", cancel: "cancelled" }[action];
      task.configuration.next_wake_at = action === "resume" ? nextWake(task.configuration.weekday, task.configuration.hour, task.configuration.minute) : null;
      for (const item of activities) if (item.context.responsibility_id === id && ["pending", "approved"].includes(item.status)) {
        item.status = "stale"; item.job_status = "cancelled";
      }
      return clone(task);
    },
    decide: async (id, action) => {
      const item = activities.find(row => row.approval_id === id);
      if (!item || item.status !== "pending") throw new Error("草案已处理");
      item.status = action === "approve" ? "approved" : "denied";
      item.context.execution_events = [...(item.context.execution_events || []), journal("approval.decision", "completed", action === "approve" ? "用户批准一次，仍未执行工具。" : "用户拒绝，计划不变。", "user")];
      item.job_status = action === "approve" ? "queued" : "cancelled";
      return { status: item.status };
    },
  };
  return { gateway, advance: () => {
    for (const item of activities) if (item.status === "approved") {
      item.context.execution_events = [...(item.context.execution_events || []),
        journal("execution.recheck", "completed", "模拟责任、日期、运动类型和计划快照复核。", "runtime"),
        journal("execution.tool", "running", "模拟开始指定日期减组工具，不写真实数据库。", "tool"),
        journal("execution.verify", "completed", "模拟写后验证，其他日期和运动类型保持不变。", "tool", { changed_date: item.input_preview.day_date, verified: true }),
        journal("execution.complete", "completed", "模拟执行结束。", "tool")];
      item.status = "executed"; item.job_status = "completed";
      item.result = { status: "adjusted", verified: true, day_date: item.input_preview.day_date };
    }
  } };
}
