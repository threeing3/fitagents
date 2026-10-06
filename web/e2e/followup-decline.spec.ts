import { expect, test } from "@playwright/test";

test("declining an outcome follow-up uses the API and remains stopped after reload", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "synthetic@example.test", username: "synthetic", display_name: "Synthetic" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Follow-up", created_at: "2026-10-02T00:00:00Z" };
  const followup = { id: "followup-one", evaluation_plan_id: "evaluation-one", status: "pending", question: { text: "你采用建议了吗？" } };
  let declined = false;
  let writes = 0;
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = route.request().method() === "GET" ? [session] : session;
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/dashboard")) body = { profile_complete: false, profile: {}, missing_slots: [], today_plan: {}, recent_memories: [], progress: {}, coach_suggestions: [] };
    else if (path === "/v1/agent/decision-followups") body = declined ? [] : [followup];
    else if (path === "/v1/agent/decision-followups/followup-one/decline") {
      expect(route.request().method()).toBe("POST");
      writes++;
      declined = true;
      body = { ...followup, status: "declined" };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "不再追问这件事" }).click();
  await expect(page.getByText("已停止这项跟进的追问；记录和风险提示保留")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("训练需求或问题")).toBeVisible();
  await expect(page.getByRole("button", { name: "不再追问这件事" })).toHaveCount(0);
  expect(writes).toBe(1);
});
