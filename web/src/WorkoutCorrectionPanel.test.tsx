import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageProvider } from "./LanguageContext";
import { WorkoutCorrectionPanel } from "./WorkoutCorrectionPanel";

const list = vi.fn(), correct = vi.fn();
vi.mock("./api", () => ({ workoutCorrectionApi: { list: (...args: unknown[]) => list(...args), correct: (...args: unknown[]) => correct(...args) } }));
const record = { id: "log-1", performed_at: "2026-10-02T10:00:00+08:00", workout_name: "慢跑", duration_minutes: 30, rpe: 6, completion_rate: null, revision: 0, correction_available: true };

function show(userId = "user-1") {
  const onRefresh = vi.fn();
  const result = render(<LanguageProvider><WorkoutCorrectionPanel userId={userId} refreshKey={0} onRefresh={onRefresh} /></LanguageProvider>);
  return { ...result, onRefresh };
}

async function chooseAndChange() {
  fireEvent.click(await screen.findByRole("button", { name: "选择更正 log-1" }));
  fireEvent.change(screen.getByLabelText("更正时长（分钟）"), { target: { value: "20" } });
  fireEvent.change(screen.getByLabelText("更正原因"), { target: { value: "核对手表" } });
  fireEvent.click(screen.getByRole("button", { name: "确认更正这条记录" }));
}

describe("saved workout corrections", () => {
  beforeEach(() => {
    sessionStorage.clear(); localStorage.removeItem("ai_fitness_language");
    list.mockReset().mockResolvedValue([record]); correct.mockReset();
  });

  it("sends only changed facts with the selected old values and revision", async () => {
    correct.mockResolvedValue({ revision: 1, audit_id: "audit-1", idempotent_replay: false });
    const view = show(); await chooseAndChange();
    await waitFor(() => expect(view.onRefresh).toHaveBeenCalledOnce());
    expect(correct.mock.calls[0]).toEqual(["log-1", {
      idempotency_key: expect.any(String), expected_revision: 0,
      expected: { duration_minutes: 30 }, changes: { duration_minutes: 20 }, reason: "核对手表",
    }]);
    expect(sessionStorage.length).toBe(0);
    expect(screen.getByRole("status")).toHaveTextContent("审计编号 audit-1");
  });

  it("freezes uncertain outcomes and restores the exact request after remount", async () => {
    correct.mockRejectedValueOnce(new Error("response lost"));
    const first = show(); await chooseAndChange();
    await screen.findByRole("alert");
    const original = correct.mock.calls[0];
    expect(screen.queryByRole("button", { name: "确认更正这条记录" })).not.toBeInTheDocument();
    first.unmount();
    correct.mockResolvedValueOnce({ revision: 1, audit_id: "audit-1", idempotent_replay: true });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "重试原更正" }));
    await waitFor(() => expect(correct).toHaveBeenCalledTimes(2));
    expect(correct.mock.calls[1]).toEqual(original);
    await waitFor(() => expect(sessionStorage.length).toBe(0));
    expect(screen.getByRole("status")).toHaveTextContent("未重复更正");
  });

  it("requires re-selection on a definitive stale-baseline rejection", async () => {
    correct.mockRejectedValue(Object.assign(new Error("revision changed"), { status: 409 }));
    show(); await chooseAndChange();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("更正被拒绝"));
    expect(screen.queryByRole("button", { name: "重试原更正" })).not.toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
    expect(correct).toHaveBeenCalledOnce();
  });

  it("does not offer correction for unassociated historical records", async () => {
    list.mockResolvedValue([{ ...record, revision: null, correction_available: false }]);
    show();
    expect(await screen.findByRole("button", { name: "选择更正 log-1" })).toBeDisabled();
    expect(correct).not.toHaveBeenCalled();
  });

  it("does not submit unchanged facts or invalid durations", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "选择更正 log-1" }));
    fireEvent.change(screen.getByLabelText("更正原因"), { target: { value: "核对" } });
    fireEvent.click(screen.getByRole("button", { name: "确认更正这条记录" }));
    expect(correct).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("更正时长（分钟）"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "确认更正这条记录" }));
    expect(correct).not.toHaveBeenCalled();
  });

  it("never loads another user's pending correction", async () => {
    sessionStorage.setItem("fitagent_pending_workout_correction_other", JSON.stringify({ record, payload: { idempotency_key: "other-key", expected_revision: 0 } }));
    show();
    await screen.findByRole("button", { name: "选择更正 log-1" });
    expect(screen.queryByRole("button", { name: "重试原更正" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem("fitagent_pending_workout_correction_other")).not.toBeNull();
  });
});
