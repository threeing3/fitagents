// Screenshot of the original frontend's explicitly synthetic responsibility demo.
import { chromium, expect } from "@playwright/test";
import { access, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const target = path.join(root, "docs/assets/fitagent-responsibility-demo.png");
try {
  await access(target);
  throw new Error("Existing screenshot preserved; choose a new output before recapturing");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
await mkdir(path.dirname(target), { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  await page.goto("http://127.0.0.1:4181/?demo=responsibilities");
  await expect(page.getByRole("heading", { name: /待审批草案/ })).toBeVisible();
  await page.locator("details").filter({ hasText: "执行过程与决策依据" }).first().evaluate(
    element => { element.open = true; },
  );
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: target, fullPage: true });
  console.log("Saved original frontend synthetic responsibility demo; no model request.");
} finally {
  await browser.close();
}
