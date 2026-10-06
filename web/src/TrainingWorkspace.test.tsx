import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TrainingWorkspace } from "./TrainingWorkspace";

vi.mock("./WorkoutView", () => ({ WorkoutView: () => <input aria-label="记录草稿" defaultValue="" /> }));
vi.mock("./ResponsibilityView", () => ({ ResponsibilityView: ({ userId }: { userId: string }) => <input aria-label="调整草稿" defaultValue={userId} /> }));

it("opens review only on request and preserves both drafts across tab switches", () => {
  render(<TrainingWorkspace session={null} userId="synthetic-user" busy={false} setBusy={vi.fn()} setNotice={vi.fn()} onRefresh={vi.fn()} />);
  expect(screen.queryByLabelText("调整草稿")).toBeNull();
  fireEvent.change(screen.getByLabelText("记录草稿"), { target: { value: "慢跑20分钟，尚未提交" } });
  fireEvent.click(screen.getByRole("tab", { name: "计划与调整" }));
  expect(screen.getByRole("tab", { name: "计划与调整" })).toHaveAttribute("aria-selected", "true");
  fireEvent.change(screen.getByLabelText("调整草稿"), { target: { value: "仍在审阅" } });
  fireEvent.click(screen.getByRole("tab", { name: "训练记录" }));
  expect(screen.getByLabelText("记录草稿")).toHaveValue("慢跑20分钟，尚未提交");
  fireEvent.click(screen.getByRole("tab", { name: "计划与调整" }));
  expect(screen.getByLabelText("调整草稿")).toHaveValue("仍在审阅");
});

it("opens the schedule from navigation and retains record drafts on return", () => {
  const props = { session: null, userId: "synthetic-user", busy: false, setBusy: vi.fn(), setNotice: vi.fn(), onRefresh: vi.fn() };
  const { rerender } = render(<TrainingWorkspace {...props} initialPane="records" />);
  fireEvent.change(screen.getByLabelText("记录草稿"), { target: { value: "未提交记录" } });
  rerender(<TrainingWorkspace {...props} initialPane="adjustments" />);
  expect(screen.getByRole("tab", { name: "计划与调整" })).toHaveAttribute("aria-selected", "true");
  rerender(<TrainingWorkspace {...props} initialPane="records" />);
  expect(screen.getByLabelText("记录草稿")).toHaveValue("未提交记录");
});
