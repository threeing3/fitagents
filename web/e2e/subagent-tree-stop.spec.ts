import { expect, test } from "@playwright/test";

test("review tree stop requires confirmation and its terminal state is read from catalog", async ({ page }) => {
  const user = { user_id: "11111111-1111-4111-8111-111111111111", email: "tree@example.test", display_name: "Tree" };
  const session = { session_id: "22222222-2222-4222-8222-222222222222", user_id: user.user_id, title: "Tree" };
  let stops = 0, finished = false;
  await page.route("**/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/v1/auth/me") body = user;
    else if (path === "/v1/chat/sessions") body = [session];
    else if (path.endsWith("/messages")) body = [];
    else if (path.endsWith("/subagents")) body = [{ parent_id: "tree-parent", revision: finished ? 3 : 2, recorded_at: "now", stop_control: { protocol: 1 }, children: [
      { child_id: "analysis", domain: "evidence_analysis", status: finished ? "failed" : "running", failure_reason: finished ? "parent_cancelled" : undefined },
    ] }];
    else if (path.endsWith("/stop")) {
      stops++;
      expect(route.request().postDataJSON()).toEqual({ expected_revision: 2, confirm_no_retry: true });
      body = { status: "cancel_requested" };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  const catalog = page.getByLabel("会话子任务目录");
  await catalog.locator(":scope > summary").click();
  await catalog.getByRole("button", { name: "停止协作", exact: true }).click();
  expect(stops).toBe(0);
  await catalog.getByRole("button", { name: "确认停止协作，不重跑" }).click();
  await expect(catalog.getByText("协作停止请求已接收；请刷新目录确认最终状态。")).toBeVisible();
  await expect(catalog.getByText("证据分析：已记录运行态，存活未核对")).toBeVisible();
  finished = true;
  await catalog.getByRole("button", { name: "刷新记录" }).click();
  await expect(catalog.getByText("证据分析：未完成（父任务取消）")).toBeVisible();
  await expect(catalog.getByRole("button", { name: "停止协作", exact: true })).toHaveCount(0);
  expect(stops).toBe(1);
});
