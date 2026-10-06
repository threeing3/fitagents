import { expect, test } from "@playwright/test";

test("real page refresh resumes owner-scoped position without cached content or writes", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "position@example.test", display_name: "Position" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "位置恢复验收" };
  const cursor = "33333333-3333-4333-8333-333333333333:1";
  const requests: string[] = [];
  const writes: string[] = [];
  await page.route("**/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") writes.push(url.pathname);
    let body: unknown = {};
    if (url.pathname === "/v1/auth/me") body = user;
    else if (url.pathname === "/v1/chat/sessions") body = [session];
    else if (url.pathname.endsWith("/messages")) body = [{ id: "m1", role: "assistant", content: "合成历史回复", agent_run_id: "run-position", execution_events: [] }];
    else if (url.pathname.endsWith("/subagents")) body = [];
    else if (url.pathname === "/v1/agent-runs/run-position/trace") body = { run_id: "run-position", status: "unconfirmed", events: [], snapshot: {}, coverage: {} };
    else if (url.pathname === "/v1/agent-runs/run-position/events") {
      requests.push(url.searchParams.get("cursor") || "beginning");
      const continued = url.searchParams.get("cursor") === cursor;
      body = { next_cursor: continued ? cursor.replace(":1", ":2") : cursor,
        has_more: false, damaged_tail: false, status: "unconfirmed",
        events: [{ position: continued ? 2 : 1, event: { type: "step", name: continued ? "after-refresh" : "before-refresh", content: "synthetic-sensitive-content" } }] };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  const expand = async () => {
    await page.getByText("完整执行追踪", { exact: true }).click();
    await page.getByText("按位置读取后续事件", { exact: true }).click();
  };
  await page.goto("/");
  await expand();
  await expect(page.getByText("1. before-refresh", { exact: true })).toBeVisible();
  const saved = await page.evaluate(() => Object.entries(sessionStorage).filter(([key]) => key.startsWith("fitagent:trace-position:v1:")));
  expect(saved).toHaveLength(1);
  expect(JSON.parse(saved[0][1])).toEqual({ version: 1, ownerId: user.user_id, runId: "run-position", cursor });
  expect(saved[0][1]).not.toContain("synthetic-sensitive-content");
  await page.reload();
  await expand();
  await expect(page.getByText(/已恢复至位置 1/)).toBeVisible();
  await expect(page.getByText("2. after-refresh", { exact: true })).toBeVisible();
  await expect(page.getByText("1. before-refresh", { exact: true })).toHaveCount(0);
  expect(requests).toEqual(["beginning", cursor]);
  expect(writes).toEqual([]);
});
