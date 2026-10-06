import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DashboardView } from "./DashboardView";
import type { Dashboard } from "./types";

const base = {
  profile_complete: true, profile: {}, missing_slots: [], today_plan: {},
  recent_memories: [], progress: {}, coach_suggestions: [], active_tasks: [],
} as unknown as Dashboard;

it("renders a dated jog as duration rather than undefined sets and reps", () => {
  render(<DashboardView dashboard={{ ...base, today_plan: { name: "慢跑", date: "2026-10-02", exercises: [{ name: "慢跑", duration_minutes: 20 }] } }} session={null} busy={false} onRefresh={vi.fn()} />);
  expect(screen.getByText("20 分钟")).toBeInTheDocument();
  expect(screen.queryByText(/undefined/)).toBeNull();
});

it("empty today does not claim that saved sessions were deleted", () => {
  render(<DashboardView dashboard={base} session={null} busy={false} onRefresh={vi.fn()} />);
  expect(screen.getByText(/今天暂无训练安排；其他日期的已保存计划仍保留/)).toBeInTheDocument();
});

it("shows zero-valued feedback and routes empty-day planning to chat", () => {
  const navigate = vi.fn();
  render(<DashboardView dashboard={{ ...base, latest_checkin: { sleep_hours: 0, fatigue: 0, soreness: 0 } } as Dashboard} session={null} busy={false} onRefresh={vi.fn()} onNavigate={navigate} />);
  expect(screen.getByText("0h")).toBeInTheDocument();
  expect(screen.getAllByText("0/10")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "说明训练需求" }));
  expect(navigate).toHaveBeenLastCalledWith("chat");
  fireEvent.click(screen.getByRole("button", { name: "在对话中说明安排" }));
  expect(navigate).toHaveBeenLastCalledWith("chat");
  fireEvent.click(screen.getByRole("button", { name: "审阅待批准调整与长期跟踪" }));
  expect(navigate).toHaveBeenLastCalledWith("responsibilities");
});
