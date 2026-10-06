import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { accountDate, PlanSchedule, ProposalDiff } from "./PlanReview";
import { createResponsibilityDemo } from "./responsibilityDemo";
import type { Dashboard } from "./types";

afterEach(() => vi.useRealTimers());
it("uses account timezone rather than machine date", () => {
  expect(accountDate("Asia/Shanghai", new Date("2026-10-03T23:30:00Z"))).toBe("2026-10-04");
  expect(accountDate("America/Los_Angeles", new Date("2026-10-03T01:30:00Z"))).toBe("2026-10-02");
});
it("shows dated sessions without guessing undated plans or completion, with week navigation", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-03T04:00:00Z"));
  const dashboard = { timezone: "Asia/Shanghai", active_plan: { plan_id: "p", status: "active", plan: { training_days: [{ date: "2026-10-04", name: "慢跑", exercises: [{ name: "慢跑", duration_minutes: 20 }] }, { day: 1, name: "无日期力量" }] } } } as Dashboard;
  render(<PlanSchedule dashboard={dashboard} />);
  expect(screen.getByText("慢跑", { selector: "summary" })).toBeInTheDocument();
  expect(screen.getByText("慢跑 · 20 分钟")).toBeInTheDocument();
  expect(screen.getByText("已保存 · 未核对完成")).toBeInTheDocument();
  expect(screen.getByText("另有 1 个未指定日期的安排")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "下一周" }));
  expect(screen.queryByText("慢跑", { selector: "summary" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "回到本周" }));
  expect(screen.getByText("慢跑", { selector: "summary" })).toBeInTheDocument();
});
it("renders exact proposal snapshots and does not invent missing differences", async () => {
  const item = (await createResponsibilityDemo().gateway.history())[0];
  const result = render(<ProposalDiff item={item} />);
  expect(screen.getAllByText("3 × 10")).toHaveLength(2);
  expect(screen.getAllByText("2 × 10")).toHaveLength(2);
  result.rerender(<ProposalDiff item={{ ...item, context: {} }} />);
  expect(screen.queryByText("2 × 10")).toBeNull();
  expect(screen.getByText(/缺少唯一的原计划/)).toBeInTheDocument();
});
