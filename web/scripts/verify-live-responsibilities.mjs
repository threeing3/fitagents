// Existing production UI + actual HTTP/PG/worker. No mocked routes or demo mode.
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const recoveryMode = process.argv.includes("--recovery");
const option = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
};
const artifact = option("--artifact", recoveryMode ? "refactor_live_recovery_ui_20261002" : "refactor_live_ui_20261002");
if (!/^[a-zA-Z0-9_-]+$/.test(artifact)) throw new Error("Artifact must be a simple filename");
const fixture = JSON.parse(await readFile(path.resolve(root, option("--fixture", "logs/refactor_live_acceptance_20261002.json")), "utf8"));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const result = { scope: "existing built frontend, synthetic account, real API and PostgreSQL", steps: [] };
try {
  await page.goto("http://127.0.0.1:1016/");
  await page.getByPlaceholder("邮箱或用户名").fill(fixture.synthetic_email);
  await page.getByPlaceholder("密码", { exact: true }).fill("Synthetic-only-password-1016");
  await page.locator("button.login-submit").click();
  await page.getByRole("button", { name: "长期跟踪", exact: true }).click();
  await expect(page.getByRole("heading", { name: "长期跟踪", exact: true })).toBeVisible();
  await expect(page.locator(".responsibility-history").filter({ hasText: "已执行" }).first()).toBeVisible();
  result.steps.push("existing UI reads persisted real execution history");

  const proposed = await page.request.post(`http://127.0.0.1:1016/v1/responsibilities/${fixture.responsibility_id}/plan-proposal`, {
    data: { plan_id: fixture.synthetic_plan_id, day_date: fixture.selected_day, reduce_by: 1, reason: "synthetic browser approval acceptance" },
  });
  expect(proposed.status()).toBe(200);
  const approval = (await proposed.json()).approval.approval_id;
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await page.getByRole("button", { name: "审阅并批准", exact: true }).click();
  await page.getByRole("button", { name: "确认批准一次", exact: true }).click();
  const row = page.locator(".responsibility-history").filter({ hasText: approval });
  await expect(row).toContainText("已批准");
  result.steps.push("browser approval persists without claiming execution");
  const worker = spawnSync(path.join(root, ".venv/Scripts/python.exe"), ["-m", "scripts.serve_refactor_acceptance", recoveryMode ? "worker-claim" : "worker", "--user-id", fixture.synthetic_user_id], { cwd: root, encoding: "utf8", timeout: 120000 });
  if (worker.status !== 0 || !worker.stdout.includes(recoveryMode ? "running" : "completed")) throw new Error("scoped worker did not reach required boundary");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  if (recoveryMode) {
    await expect(row).toContainText("任务已领取");
    await row.getByRole("button", { name: "核对中断执行", exact: true }).click();
    const reconciled = page.waitForResponse(response => response.url().endsWith("/reconcile") && response.request().method() === "POST");
    await page.getByRole("button", { name: "确认核对，不重跑", exact: true }).click();
    const response = await reconciled;
    expect(response.status()).toBe(200);
    const outcome = await response.json();
    expect(outcome).toMatchObject({ status: "failed", requeued: false, known_uncommitted: true });
    await expect(row).toContainText("执行失败");
    await row.locator(".execution-timeline > summary").click();
    await expect(row).toContainText("关闭本次执行，不自动重跑");
    result.steps.push("actual browser reconciliation confirms uncommitted adjustment without replay");
    await page.reload();
    await page.getByRole("button", { name: "长期跟踪", exact: true }).click();
    await expect(page.locator(".responsibility-history").filter({ hasText: approval })).toContainText("执行失败");
    const claimAgain = spawnSync(path.join(root, ".venv/Scripts/python.exe"), ["-m", "scripts.serve_refactor_acceptance", "worker", "--user-id", fixture.synthetic_user_id], { cwd: root, encoding: "utf8", timeout: 120000 });
    expect(claimAgain.status).toBe(0);
    expect(claimAgain.stdout).toContain("False");
    result.steps.push("recovery survives reload and scoped worker finds no task to replay");
  } else {
  await expect(row).toContainText("已执行");
  await row.locator(".execution-timeline > summary").click();
  await expect(row).toContainText("写后重新读取计划");
  await expect(row).toContainText("不是模型内部完整思考");
  result.steps.push("real worker execution and persisted harness events visible after refresh");
  await page.reload();
  await page.getByRole("button", { name: "长期跟踪", exact: true }).click();
  await expect(page.locator(".responsibility-history").filter({ hasText: approval })).toContainText("已执行");
  }
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(root, `logs/${artifact}.png`), fullPage: true });
  result.steps.push("execution survives reload; no browser errors");
  result.status = "passed";
  console.log(JSON.stringify(result));
} catch (error) {
  result.status = "failed";
  result.failure = error.message;
  throw error;
} finally {
  await writeFile(path.join(root, `logs/${artifact}.json`), JSON.stringify(result, null, 2));
  await browser.close();
}
