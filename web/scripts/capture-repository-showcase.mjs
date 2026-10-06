// Capture the actual frontend with its explicitly synthetic built-in fixture.
import { chromium } from "@playwright/test";
import { access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const target = path.join(root, "docs/assets/fitagent-workspace-20261006.png");
try {
  await access(target);
  throw new Error("Screenshot exists; choose a new filename rather than overwriting.");
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  await page.goto("http://127.0.0.1:4174/?demo=responsibilities");
  await page.getByRole("heading", { name: /待审批草案/ }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: target });
  console.log("Saved current original frontend, synthetic fixture, no paid model or patient data.");
} finally {
  await browser.close();
}
