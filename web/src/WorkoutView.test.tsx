import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "./LanguageContext";
import { WorkoutView } from "./WorkoutView";

const logWorkout = vi.fn();

vi.mock("./api", () => ({
  logWorkout: (...args: unknown[]) => logWorkout(...args),
  workoutCorrectionApi: { list: async () => [], correct: vi.fn() },
}));

const session = { session_id: "session-1", user_id: "user-1" };

function renderWorkout() {
  const setBusy = vi.fn();
  const setNotice = vi.fn();
  const onRefresh = vi.fn();
  render(
    <LanguageProvider>
      <WorkoutView
        session={session}
        busy={false}
        setBusy={setBusy}
        setNotice={setNotice}
        onRefresh={onRefresh}
      />
    </LanguageProvider>,
  );
  return { setBusy, setNotice, onRefresh };
}

describe("WorkoutView", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.removeItem("ai_fitness_language");
    logWorkout.mockReset();
  });

  it("creates a fresh request key only after the previous workout succeeds", async () => {
    logWorkout.mockResolvedValue({ status: "recorded", workout_log_id: "log-1", idempotent_replay: false });
    renderWorkout();
    fireEvent.change(screen.getByRole("textbox", { name: "动作 1" }), { target: { value: "哑铃划船" } });
    fireEvent.click(screen.getByRole("button", { name: "保存训练" }));

    await waitFor(() => expect(logWorkout).toHaveBeenCalledTimes(1));
    const firstKey = logWorkout.mock.calls[0][2];
    expect(firstKey).toEqual(expect.any(String));
    expect(logWorkout.mock.calls[0][1]).toMatchObject({
      workout_name: "力量训练",
      duration_minutes: 45,
      rpe: 6,
      completion_rate: 1,
    });
    await waitFor(() => expect(sessionStorage.length).toBe(0));

    fireEvent.click(screen.getByRole("button", { name: "记录下一场训练" }));
    fireEvent.change(screen.getByRole("textbox", { name: "动作 1" }), { target: { value: "深蹲" } });
    fireEvent.click(screen.getByRole("button", { name: "保存训练" }));
    await waitFor(() => expect(logWorkout).toHaveBeenCalledTimes(2));
    expect(logWorkout.mock.calls[1][2]).not.toBe(firstKey);
  });

  it("freezes an uncertain workout and retries the exact payload with the same key", async () => {
    logWorkout
      .mockRejectedValueOnce(new Error("connection reset after commit"))
      .mockResolvedValueOnce({ status: "recorded", workout_log_id: "log-1", idempotent_replay: true });
    renderWorkout();
    fireEvent.change(screen.getByRole("textbox", { name: "动作 1" }), { target: { value: "卧推" } });
    fireEvent.click(screen.getByRole("button", { name: "保存训练" }));

    await screen.findByRole("alert");
    expect(screen.getByRole("textbox", { name: "动作 1" })).toBeDisabled();
    const firstPayload = logWorkout.mock.calls[0][1];
    const firstKey = logWorkout.mock.calls[0][2];
    expect(sessionStorage.length).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
    await waitFor(() => expect(logWorkout).toHaveBeenCalledTimes(2));
    expect(logWorkout.mock.calls[1][1]).toEqual(firstPayload);
    expect(logWorkout.mock.calls[1][2]).toBe(firstKey);
    await waitFor(() => expect(sessionStorage.length).toBe(0));
  });

  it("restores an unresolved request after a page reload", async () => {
    const pending = {
      key: "persisted-key",
      createdAt: "2026-09-21T00:00:00.000Z",
      payload: {
        workout_name: "恢复训练",
        duration_minutes: 30,
        rpe: 5,
        completion_rate: 0.8,
        exercises: [{ name: "硬拉", sets: [{ reps: 5, weight: 60, rpe: 5, completed: true }] }],
      },
    };
    sessionStorage.setItem("fitagent_pending_workout_user-1", JSON.stringify(pending));
    logWorkout.mockResolvedValue({ status: "recorded", workout_log_id: "log-2", idempotent_replay: true });
    renderWorkout();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试原提交" }));
    await waitFor(() => expect(logWorkout).toHaveBeenCalledWith("user-1", pending.payload, "persisted-key"));
  });
});
