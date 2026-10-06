import { expect, test } from "@playwright/test";

test("original workout page corrects an explicit record and recovers a lost response", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "synthetic@example.test", username: "synthetic", display_name: "Synthetic" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Workouts", created_at: "2026-10-03T00:00:00Z" };
  const record = { id: "33333333-3333-4333-8333-333333333333", performed_at: "2026-10-02T10:00:00+08:00", workout_name: "慢跑", duration_minutes: 30, rpe: 6, completion_rate: 1, revision: 0, correction_available: true };
  const requests: unknown[] = [];
  let writes = 0;
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/dashboard")) body = { profile_complete: false, profile: {}, missing_slots: [], today_plan: {}, recent_memories: [], progress: {}, coach_suggestions: [] };
    else if (path === "/v1/agent/decision-followups") body = [];
    else if (path === "/v1/workouts/logs") body = [record];
    else if (path.endsWith("/corrections")) {
      expect(path).toBe(`/v1/workouts/logs/${record.id}/corrections`);
      requests.push(route.request().postDataJSON());
      if (requests.length === 1) {
        writes++; record.duration_minutes = 20; record.revision = 1;
        await route.abort("connectionreset"); return;
      }
      expect(requests[1]).toEqual(requests[0]);
      body = { status: "corrected", revision: 1, audit_id: "audit-one", idempotent_replay: true };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "训练记录", exact: true }).click();
  await page.getByRole("button", { name: `选择更正 ${record.id}` }).click();
  await page.getByLabel("更正时长（分钟）").fill("20");
  await page.getByLabel("更正原因").fill("核对手表");
  await page.getByRole("button", { name: "确认更正这条记录" }).click();
  await expect(page.getByText(/结果未确认，请重试原更正/)).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "训练记录", exact: true }).click();
  await page.getByRole("button", { name: "重试原更正" }).click();
  await expect(page.getByText(/审计编号 audit-one/)).toBeVisible();
  expect(writes).toBe(1);
  expect(requests[0]).toMatchObject({ expected_revision: 0, expected: { duration_minutes: 30 }, changes: { duration_minutes: 20 }, reason: "核对手表" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
