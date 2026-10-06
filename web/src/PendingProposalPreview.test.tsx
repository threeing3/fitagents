import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { responsibilityApi } from "./api";
import { createResponsibilityDemo } from "./responsibilityDemo";
import { PendingProposalPreview } from "./PendingProposalPreview";

it("shows real pending snapshots and navigates without granting approval", async () => {
  const demo = createResponsibilityDemo();
  vi.spyOn(responsibilityApi, "history").mockImplementation(demo.gateway.history);
  const decide = vi.spyOn(responsibilityApi, "decide");
  const review = vi.fn();
  render(<PendingProposalPreview userId="synthetic" onReview={review} />);
  await screen.findByRole("region", { name: "有调整待你审阅" });
  expect(screen.getAllByText("2 × 10")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "审阅调整" }));
  expect(review).toHaveBeenCalledOnce();
  expect(decide).not.toHaveBeenCalled();
});

it("opens the original saved plan without deciding the proposal", async () => {
  const demo = createResponsibilityDemo();
  vi.spyOn(responsibilityApi, "history").mockImplementation(demo.gateway.history);
  const decide = vi.spyOn(responsibilityApi, "decide");
  const viewPlan = vi.fn();
  render(<PendingProposalPreview userId="synthetic" onReview={vi.fn()} onViewPlan={viewPlan} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原计划" }));
  expect(viewPlan).toHaveBeenCalledOnce();
  expect(decide).not.toHaveBeenCalled();
});
