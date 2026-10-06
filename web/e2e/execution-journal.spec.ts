import { expect, test } from "@playwright/test";

test("reconciliation requires confirmation, refuses live execution and never retries", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "recover@example.test", display_name: "Synthetic recovery" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Synthetic recovery" };
  let requests = 0, released = false, reconciled = false;
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/subagents")) body = [{ parent_id: "synthetic-parent", revision: reconciled ? 2 : 1, recorded_at: "2026-10-04T00:00:00Z", execution_lease: { protocol: 1 }, children: [
      { child_id: "one", domain: "training", status: reconciled ? "failed" : "running", failure_reason: reconciled ? "execution_interrupted" : undefined },
    ] }];
    else if (path.endsWith("/reconcile")) {
      requests++;
      expect(route.request().postDataJSON()).toEqual({ expected_revision: 1, confirm_no_retry: true });
      if (!released) {
        await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "execution_busy" }) });
        return;
      }
      reconciled = true;
      body = { changed: true, requeued: false, revision: 2 };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await catalog.getByRole("button", { name: "核对遗留状态" }).click();
  expect(requests).toBe(0);
  await catalog.getByRole("button", { name: "确认核对，不重跑" }).click();
  await expect(catalog.getByRole("alert")).toContainText("未重新运行任务");
  await expect(catalog.getByText("训练：已记录运行态，存活未核对")).toBeVisible();
  released = true;
  await catalog.getByRole("button", { name: "确认核对，不重跑" }).click();
  await expect(catalog.getByText("训练：未完成（已核对执行中断）")).toBeVisible();
  expect(requests).toBe(2);
});

test("session child catalog survives refresh and does not infer process liveness", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "catalog@example.test", display_name: "Synthetic catalog" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Synthetic catalog" };
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/subagents")) body = [{ parent_id: "synthetic-parent", revision: 3, recorded_at: "2026-10-04T00:00:00Z", children: [
      { child_id: "one", domain: "evidence_analysis", status: "failed", failure_reason: "parent_cancelled" },
      { child_id: "two", domain: "training", status: "running" },
    ] }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await expect(catalog.getByText("证据分析：未完成（父任务取消）")).toBeVisible();
  await expect(catalog.getByText("训练：已记录运行态，存活未核对")).toBeVisible();
  await page.reload();
  await catalog.locator(":scope > summary").click();
  await expect(catalog.getByText("证据分析：未完成（父任务取消）")).toBeVisible();
  await catalog.getByRole("button", { name: "刷新记录" }).click();
  await expect(catalog.getByText("训练：已记录运行态，存活未核对")).toBeVisible();
  await page.screenshot({ path: "../logs/subagent_catalog_browser_20261004.png", fullPage: true });
});

test("responsibility history exposes rule, authorization, tool and verification events", async ({ page }) => {
  await page.goto("/?demo=responsibilities");
  await page.getByRole("button", { name: "审阅并批准" }).click();
  await page.getByRole("button", { name: "确认批准一次" }).click();
  await expect(page.getByText("已批准 · 等待后台执行", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "推进演示后台" }).click();
  const history = page.locator(".responsibility-history");
  await history.locator(".execution-timeline > summary").click();
  await expect(history.getByText("规则判断", { exact: true })).toBeVisible();
  await expect(history.getByText("用户决定", { exact: true })).toBeVisible();
  await expect(history.getByText(/模拟写后验证/)).toBeVisible();
  await expect(history.getByText(/不是模型内部完整思考/)).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 1800 });
  await history.screenshot({ path: "../logs/execution_journal_browser_20260930.png" });
});

test("chat shows streamed public events and restores them from saved message history", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "journal@example.test", username: "journal", display_name: "Journal" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Journal", created_at: "2026-09-30T00:00:00Z" };
  const entry = { type: "execution_event", name: "command.route", status: "completed", source: "rule", recorded_at: "2026-09-30T02:00:00Z", summary: "按明确模板解析训练复盘委托；未调用模型推断授权。", details: {} };
  let saved = false;
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = saved ? [
      { id: "saved-user", role: "user", content: "创建4周每周日18:00训练复盘", execution_events: [] },
      { id: "saved-assistant", role: "assistant", content: "责任已保存，计划不会自动修改。", execution_events: [entry] },
    ] : [];
    else if (path === "/v1/chat/messages/stream") {
      saved = true;
      await route.fulfill({ status: 200, contentType: "application/x-ndjson", body: [entry,
        { type: "answer_delta", text: "责任已保存，计划不会自动修改。" },
        { type: "done", run_id: "33333333-3333-4333-8333-333333333333", state_updates: {}, tool_calls: [] },
      ].map(item => JSON.stringify(item)+"\n").join("") });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await page.getByLabel("训练需求或问题").fill("创建4周每周日18:00训练复盘");
  await expect(page.locator(".send-btn")).toBeEnabled();
  await page.locator(".send-btn").click();
  await expect(page.getByText("责任已保存，计划不会自动修改。", { exact: true })).toBeVisible();
  await page.locator(".execution-timeline > summary").click();
  await expect(page.locator(".execution-line-summary").filter({ hasText: entry.summary })).toBeVisible();
  await page.reload();
  await page.locator(".execution-timeline > summary").click();
  await expect(page.locator(".execution-line-summary").filter({ hasText: entry.summary })).toBeVisible();
  await expect(page.getByText("规则判断", { exact: true })).toBeVisible();
});
