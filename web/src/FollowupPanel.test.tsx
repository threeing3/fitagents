import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { followupApi } from "./api";
import { FollowupPanel } from "./FollowupPanel";

vi.mock("./api", () => ({ followupApi: { list: vi.fn(), decline: vi.fn() } }));
const item = { id: "one", evaluation_plan_id: "plan-one", status: "pending", question: { text: "采用建议了吗？" } };
beforeEach(() => { vi.resetAllMocks(); });

it("shows stop only after server confirmation and removes that evaluation", async () => {
  vi.mocked(followupApi.list).mockResolvedValue([item]);
  vi.mocked(followupApi.decline).mockResolvedValue({ ...item, status: "declined" });
  render(<FollowupPanel refreshKey={1} />);
  fireEvent.click(await screen.findByRole("button", { name: "不再追问这件事" }));
  await screen.findByRole("status");
  expect(followupApi.decline).toHaveBeenCalledWith("one");
  expect(screen.queryByRole("button", { name: "不再追问这件事" })).toBeNull();
});

it("retains question when stopping fails", async () => {
  vi.mocked(followupApi.list).mockResolvedValue([item]);
  vi.mocked(followupApi.decline).mockRejectedValue(new Error("lost response"));
  render(<FollowupPanel refreshKey={1} />);
  fireEvent.click(await screen.findByRole("button", { name: "不再追问这件事" }));
  await screen.findByRole("alert");
  await waitFor(() => expect(screen.getByRole("button", { name: "不再追问这件事" })).not.toBeDisabled());
  expect(screen.queryByRole("status")).toBeNull();
});
