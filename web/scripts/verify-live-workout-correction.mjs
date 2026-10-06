// Actual built application and isolated database; no route mocks.
import { chromium, expect } from "@playwright/test";
import { access, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const artifact = process.argv[2] || "workout_correction_live_20261003_final";
if (!/^[a-zA-Z0-9_-]+$/.test(artifact)) throw new Error("Invalid artifact name");
const report = path.join(root, "logs", `${artifact}.json`);
try { await access(report); throw new Error("Report already exists"); }
catch (error) { if (error.code !== "ENOENT") throw error; }
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const result = { scope: "synthetic account, actual application, original UI, isolated PostgreSQL", steps: [] };
const base = "http://127.0.0.1:1016";
try {
  const email = `correction-${Date.now()}@example.com`;
  const password = "Synthetic-only-password-1016";
  const registered = await page.request.post(`${base}/v1/auth/register`, { data: { email, password } });
  expect(registered.status()).toBe(201);
  const identity = await registered.json();
  result.synthetic_user_id = identity.user_id;
  const created = await page.request.post(`${base}/v1/workouts/logs`, {
    data: { workout_name: "收口验收慢跑", duration_minutes: 30, rpe: 6, completion_rate: 1, idempotency_key: "live-create-one" },
  });
  expect(created.status()).toBe(200);
  const logId = (await created.json()).workout_log_id;
  result.workout_log_id = logId;
  result.steps.push("actual API creates one owned workout with explicit source association");
  await page.goto(base);
  if (await page.getByPlaceholder("邮箱或用户名").isVisible()) {
    await page.getByPlaceholder("邮箱或用户名").fill(email);
    await page.getByPlaceholder("密码", { exact: true }).fill(password);
    await page.locator("button.login-submit").click();
  }
  await page.getByRole("button", { name: "训练记录", exact: true }).click();
  await page.getByRole("button", { name: `选择更正 ${logId}` }).click();
  await page.getByLabel("更正时长（分钟）").fill("20");
  await page.getByLabel("更正原因").fill("合成验收：核对计时器");
  await page.getByRole("button", { name: "确认更正这条记录" }).click();
  await expect(page.getByText(/更正已确认，版本 1/)).toBeVisible();
  result.steps.push("original frontend confirms a specific correction through real API");
  await page.reload();
  await page.getByRole("button", { name: "训练记录", exact: true }).click();
  const row = page.locator(".workout-correction li").filter({ hasText: logId });
  await expect(row).toContainText("20 min");
  await expect(row).toContainText("版本 1");
  result.steps.push("reload reads persisted corrected facts and revision");
  const payload = { idempotency_key: "live-concurrent-correction", expected_revision: 1, expected: { duration_minutes: 20 }, changes: { duration_minutes: 25 }, reason: "合成并发验收" };
  const responses = await Promise.all([1, 2].map(() => page.request.post(`${base}/v1/workouts/logs/${logId}/corrections`, { data: payload })));
  expect(responses.map(response => response.status())).toEqual([200, 200]);
  const receipts = await Promise.all(responses.map(response => response.json()));
  expect(receipts[0].audit_id).toBe(receipts[1].audit_id);
  expect(receipts.map(item => item.idempotent_replay).sort()).toEqual([false, true]);
  expect(receipts.map(item => item.revision)).toEqual([2, 2]);
  result.concurrent_receipts = receipts;
  result.steps.push("two concurrent real HTTP requests share one committed correction receipt");
  const stale = await page.request.post(`${base}/v1/workouts/logs/${logId}/corrections`, { data: { ...payload, idempotency_key: "live-stale" } });
  expect(stale.status()).toBe(409);
  const records = await (await page.request.get(`${base}/v1/workouts/logs`)).json();
  expect(records).toHaveLength(1);
  expect(records[0]).toMatchObject({ id: logId, duration_minutes: 25, revision: 2 });
  result.steps.push("stale baseline rejected and one final workout remains");
  await page.getByRole("button", { name: "刷新训练记录" }).click();
  await expect(row).toContainText("25 min");
  await expect(row).toContainText("版本 2");
  await page.screenshot({ path: path.join(root, "logs", `${artifact}.png`), fullPage: true });
  expect(errors).toEqual([]);
  result.status = "passed";
} catch (error) { result.status = "failed"; result.error = String(error); process.exitCode = 1; }
finally { result.browser_errors = errors; await writeFile(report, JSON.stringify(result, null, 2)); await browser.close(); }
console.log(JSON.stringify(result));
