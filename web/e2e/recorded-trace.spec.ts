import { expect, test } from "@playwright/test";

test("recorded trace survives history reload, expands evidence and never writes", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "trace@example.test", display_name: "Trace" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "执行追踪验收" };
  let reads = 0;
  const writes: string[] = [];
  await page.route("**/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") writes.push(path);
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [{ id: "m1", role: "assistant", content: "已记录训练，饮食建议未改动计划。", agent_run_id: "run-trace", execution_events: [] }];
    else if (path.endsWith("/subagents")) body = [];
    else if (path === "/v1/agent-runs/run-trace/trace") {
      reads++;
      body = { run_id: "run-trace", status: "completed", events: [
        { event_id: "e1", order: 1, parent_id: "run-trace", child_id: "nutrition", name: "subagent.result", status: "completed", source: "journal", latency_ms: 10, recorded_at: "2026-10-04T15:00:00Z", summary: "饮食子任务依据有效记录返回建议", input: { date: "2026-10-04" }, output: { evidence_ids: ["memory-7"], recommendations: ["维持既定安排，待用户确认"] } },
      ], snapshot: { config: { llm_provider: "synthetic" } }, coverage: {} };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await expect(page.getByText("完整执行追踪", { exact: true })).toBeVisible();
  expect(reads).toBe(0);
  await page.getByText("完整执行追踪", { exact: true }).click();
  await page.getByText("1. subagent.result · completed · 10 ms", { exact: true }).click();
  await expect(page.locator(".recorded-trace pre").filter({ hasText: "memory-7" })).toBeVisible();
  await page.getByRole("button", { name: "刷新记录", exact: true }).click();
  await expect.poll(() => reads).toBe(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "展开或收起导航" }).click();
  await expect(page.getByText("完整执行追踪", { exact: true })).toBeVisible();
  const overflow = await page.locator(".recorded-trace").evaluate(element => element.scrollWidth > element.clientWidth + 1);
  expect(overflow).toBe(false);
  await page.screenshot({ path: "../logs/recorded_trace_mobile_20261004_r3.png", fullPage: true });
  await page.reload();
  await expect(page.getByText("完整执行追踪", { exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});
