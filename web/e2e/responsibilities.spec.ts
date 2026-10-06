import { expect, test } from "@playwright/test";
import { createResponsibilityDemo } from "../src/responsibilityDemo";

test("isolated demo renders, scopes approval and keeps execution separate", async ({ page }) => {
  const backendRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/v1/")) backendRequests.push(request.url()); });
  await page.goto("/?demo=responsibilities");
  await expect(page.getByRole("heading", { name: "长期跟踪" })).toBeVisible();
  await expect(page.getByText(/不连接业务数据库/)).toBeVisible();
  await page.getByRole("button", { name: "审阅并批准" }).click();
  await page.getByRole("button", { name: "确认批准一次" }).click();
  await expect(page.getByText("已批准 · 等待后台执行", { exact: true })).toBeVisible();
  await expect(page.getByText("已执行 · 计划校验通过", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "推进演示后台" }).click();
  await expect(page.getByText("已执行 · 计划校验通过", { exact: true })).toBeVisible();
  expect(backendRequests).toEqual([]);
  await page.screenshot({ path: "../logs/responsibility_frontend_desktop_20260930.png", fullPage: true });
  await page.locator(".responsibility-view").evaluate(element => { element.scrollTop = element.scrollHeight; });
  await page.screenshot({ path: "../logs/responsibility_frontend_execution_20260930.png", fullPage: true });
  await page.locator(".responsibility-view").evaluate(element => { element.scrollTop = 0; });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "刷新状态" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "../logs/responsibility_frontend_mobile_20260930.png", fullPage: true });
});

test("authenticated navigation uses live API contracts rather than the demo adapter", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "synthetic@example.test", username: "synthetic", display_name: "Synthetic" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Synthetic", created_at: "2026-09-30T00:00:00Z" };
  const demo = createResponsibilityDemo();
  const decisions: unknown[] = [];
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path === "/v1/responsibilities") body = await demo.gateway.list();
    else if (path === "/v1/approvals/history") body = await demo.gateway.history();
    else if (path === "/v1/approvals/decide") {
      expect(route.request().method()).toBe("POST");
      const payload = route.request().postDataJSON();
      decisions.push(payload);
      body = await demo.gateway.decide(payload.approval_id, payload.action);
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "长期跟踪", exact: true }).click();
  await expect(page.getByRole("heading", { name: "长期跟踪" })).toBeVisible();
  await expect(page.getByText(/不连接业务数据库/)).toHaveCount(0);
  await page.getByRole("button", { name: "审阅并批准" }).click();
  await page.getByRole("button", { name: "确认批准一次" }).click();
  await expect(page.getByText("已批准 · 等待后台执行", { exact: true })).toBeVisible();
  expect(decisions).toEqual([{ approval_id: "demo-approval", action: "approve" }]);
  demo.advance();
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(page.getByText("已执行 · 计划校验通过", { exact: true })).toBeVisible();
});
