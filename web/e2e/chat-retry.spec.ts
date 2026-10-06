import { expect, test } from "@playwright/test";

for (const outcome of ["completed", "unconfirmed"] as const) {
  test(`lost stream response recovers ${outcome} evidence without another execution`, async ({ page }) => {
    const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "synthetic@example.test", username: "synthetic", display_name: "Synthetic" };
    const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Status replay", created_at: "2026-09-27T00:00:00Z" };
    let executions = 0;
    let requestKey = "";
    let lookups = 0;
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/v1/auth/me") body = user;
      else if (path === "/v1/chat/sessions") body = route.request().method() === "GET" ? [session] : session;
      else if (path.endsWith("/messages")) body = [];
      else if (path.endsWith("/dashboard")) body = { profile_complete: false, profile: {}, missing_slots: [], today_plan: {}, recent_memories: [], progress: {}, coach_suggestions: [] };
      else if (path === "/v1/chat/messages/stream") {
        executions++;
        requestKey = route.request().postDataJSON().idempotency_key;
        await route.fulfill({ status: 200, contentType: "application/x-ndjson", body: '{"type":"answer_delta","text":"处理中"}\n' });
        return;
      } else if (path === "/v1/chat/requests/status") {
        lookups++;
        expect(route.request().method()).toBe("GET");
        expect(route.request().headers()["idempotency-key"]).toBe(requestKey);
        body = {
          status: outcome,
          may_repeat_writes: false,
          confirmed_writes: [{ kind: "workout_log", record_id: "saved", workout_name: "跑步", duration_minutes: 30 }],
          ...(outcome === "completed" ? { assistant_message: "已保存的完整回复", agent_run_id: "33333333-3333-4333-8333-333333333333" } : {}),
        };
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/");
    await page.getByLabel("训练需求或问题").fill("我刚完成跑步30分钟，帮我记录");
    await page.locator(".send-btn").click();
    await expect(page.getByText(outcome === "completed" ? "已保存的完整回复" : /已确认保存：跑步 30 分钟/)).toBeVisible();
    expect(executions).toBe(1);
    expect(lookups).toBe(1);
    const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem("fitagent.pending-chat.v1") || "[]"));
    expect(pending).toHaveLength(outcome === "completed" ? 0 : 1);
  });
}

test("reload keeps failed chat identity; completed new send gets a new identity", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "synthetic@example.test", username: "synthetic", display_name: "Synthetic" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Retry replay", created_at: "2026-09-27T00:00:00Z" };
  const keys: string[] = [];
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = route.request().method() === "GET" ? [session] : session;
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/dashboard")) body = { profile_complete: false, profile: {}, missing_slots: [], today_plan: {}, recent_memories: [], progress: {}, coach_suggestions: [] };
    else if (path === "/v1/chat/messages/stream") {
      keys.push(route.request().postDataJSON().idempotency_key);
      const events = [{ type: "answer_delta", text: "收到。" }];
      if (keys.length > 1) events.push({ type: "done", run_id: "33333333-3333-4333-8333-333333333333" } as any);
      await route.fulfill({ status: 200, contentType: "application/x-ndjson", body: events.map((event) => JSON.stringify(event)).join("\n") + "\n" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  const send = async () => {
    await page.getByLabel("训练需求或问题").fill("我刚完成30分钟哑铃训练，帮我记录");
    await page.locator(".send-btn").click();
  };
  await page.goto("/");
  await send();
  await expect(page.getByText(/Request failed:/)).toBeVisible();
  expect(keys[0]).toBeTruthy();
  await page.reload();
  await send();
  await expect(page.getByText("回复已保存，智能体会继续维护档案与记忆。", { exact: true })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  await send();
  await expect.poll(() => keys.length).toBe(3);
  expect(keys[2]).not.toBe(keys[1]);
});
