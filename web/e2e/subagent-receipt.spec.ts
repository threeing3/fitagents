import { expect, test } from "@playwright/test";

test("stop is explicitly confirmed and final cancellation is obtained from the receipt", async ({ page }) => {
  const owner = "11111111-1111-4111-8111-111111111111";
  const session = "22222222-2222-4222-8222-222222222222";
  const key = "12345678-1234-4123-8123-123456789abc";
  let stops = 0;
  await page.addInitScript(({ owner, session, key }) => {
    sessionStorage.setItem(`fitagent:child-receipt:v1:${owner}:${session}:start`, key);
  }, { owner, session, key });
  await page.route("**/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = { user_id: owner, email: "stop@example.test", display_name: "Stop" };
    else if (path === "/v1/chat/sessions") body = [{ session_id: session, user_id: owner, title: "Stop" }];
    else if (path.endsWith("/messages") || path.endsWith("/subagents")) body = [];
    else if (path.endsWith("/subagent-requests/cancel")) {
      stops++;
      expect(request.headers()["idempotency-key"]).toBe(key);
      expect(request.postDataJSON()).toEqual({ confirm_no_retry: true });
      body = { status: "cancel_requested", no_automatic_retry: true };
    } else if (path.endsWith("/subagent-requests/status")) {
      expect(request.headers()["idempotency-key"]).toBe(key);
      body = { status: "recorded", result: { status: "failed", failure_reason: "user_cancelled", no_business_writes: true } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await catalog.getByText("独立领域咨询", { exact: true }).click();
  await catalog.getByRole("button", { name: "请求停止", exact: true }).click();
  expect(stops).toBe(0);
  await catalog.getByRole("button", { name: "确认停止，不重跑" }).click();
  await expect(catalog.getByText("已收到停止请求；是否已停止，以最终回执为准。")).toBeVisible();
  await expect(catalog.getByRole("button", { name: "启动只读咨询" })).toBeDisabled();
  await catalog.getByRole("button", { name: "查询原请求" }).click();
  await expect(catalog.getByText("本次咨询已停止，未执行变更")).toBeVisible();
  expect(stops).toBe(1);
});

test("an old continuation receipt remains discoverable outside the catalog", async ({ page }) => {
  const owner = "11111111-1111-4111-8111-111111111111";
  const sessionId = "22222222-2222-4222-8222-222222222222";
  const key = "12345678-1234-4123-8123-123456789abc";
  let posts = 0;
  await page.addInitScript(({ owner, sessionId, key }) => {
    sessionStorage.setItem(`fitagent:child-receipt:v1:${owner}:${sessionId}:old-parent`, key);
  }, { owner, sessionId, key });
  await page.route("**/v1/**", async route => {
    const request = route.request();
    if (request.method() === "POST") posts++;
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = { user_id: owner, email: "old@example.test", display_name: "Old receipt" };
    else if (path === "/v1/chat/sessions") body = [{ session_id: sessionId, user_id: owner, title: "Old receipt" }];
    else if (path.endsWith("/messages") || path.endsWith("/subagents")) body = [];
    else if (path.endsWith("/subagent-requests/status")) {
      expect(request.headers()["idempotency-key"]).toBe(key);
      body = { status: "recorded", result: { status: "completed", no_business_writes: true, advice: { summary: "旧任务合成回执" } } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await catalog.getByText("目录外待确认请求 · 1").click();
  await catalog.getByRole("button", { name: "查询原请求" }).click();
  await expect(catalog.getByText("旧任务合成回执")).toBeVisible();
  await expect(catalog.getByRole("button", { name: "继续只读咨询" })).toHaveCount(0);
  expect(posts).toBe(0);
});

test("disconnected child request survives page reload without model resubmission", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "receipt@example.test", display_name: "Synthetic receipt" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Synthetic receipt" };
  let submissions = 0;
  let originalKey = "";
  await page.route("**/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/subagents") && request.method() === "GET") body = [];
    else if (path.endsWith("/subagents") && request.method() === "POST") {
      submissions++;
      originalKey = request.headers()["idempotency-key"];
      expect(request.postDataJSON()).toEqual({ role: "training", message: "合成咨询：调整训练建议" });
      await route.abort("failed");
      return;
    } else if (path.endsWith("/subagent-requests/status")) {
      expect(request.headers()["idempotency-key"]).toBe(originalKey);
      body = { status: "recorded", result: { status: "completed", no_business_writes: true, advice: { summary: "合成回执建议，不是模型效果证据" } } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await catalog.getByText("独立领域咨询", { exact: true }).click();
  await catalog.getByLabel("子任务问题").fill("合成咨询：调整训练建议");
  await catalog.getByRole("button", { name: "启动只读咨询" }).click();
  await expect(catalog.getByRole("button", { name: "查询原请求" })).toBeVisible();
  await page.reload();
  await catalog.locator(":scope > summary").click();
  await catalog.getByText("独立领域咨询", { exact: true }).click();
  await expect(catalog.getByRole("button", { name: "启动只读咨询" })).toBeDisabled();
  await catalog.getByRole("button", { name: "查询原请求" }).click();
  await expect(catalog.getByText("合成回执建议，不是模型效果证据")).toBeVisible();
  expect(submissions).toBe(1);
});
