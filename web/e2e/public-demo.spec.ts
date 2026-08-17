import { expect, test, type Page } from "@playwright/test";

const user = {
  user_id: "11111111-1111-4111-8111-111111111111",
  email: "candidate@example.com",
  username: "candidate",
  display_name: "候选人",
  avatar_url: null,
  created_at: "2026-08-09T00:00:00Z",
  access_token: "api-compatible-token",
};

async function mockApi(page: Page) {
  let authenticated = false;
  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/v1/auth/me") {
      await route.fulfill({
        status: authenticated ? 200 : 401,
        contentType: "application/json",
        body: authenticated ? JSON.stringify(user) : JSON.stringify({ error: { message: "unauthorized" } }),
      });
      return;
    }
    if (path === "/v1/auth/register") {
      const payload = request.postDataJSON();
      expect(payload.invite_code).toBe("interview-demo");
      authenticated = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "set-cookie": "ai_fitness_session=mock; HttpOnly; SameSite=Lax; Path=/" },
        body: JSON.stringify(user),
      });
      return;
    }
    if (path === "/v1/chat/sessions" && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      return;
    }
    if (path === "/v1/chat/sessions" && request.method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "22222222-2222-4222-8222-222222222222",
          user_id: user.user_id,
          title: "AI Coach Session",
          created_at: "2026-08-09T00:00:00Z",
        }),
      });
      return;
    }
    if (path.includes("/messages") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      return;
    }
    if (path === "/v1/chat/messages/stream") {
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body: [
          JSON.stringify({ type: "status", text: "安全检查" }),
          JSON.stringify({ type: "answer_delta", text: "先从可执行的小目标开始。" }),
          JSON.stringify({ type: "done", run_id: "33333333-3333-4333-8333-333333333333" }),
          "",
        ].join("\n"),
      });
      return;
    }
    if (path.endsWith("/dashboard")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          profile_complete: false,
          profile: {},
          missing_slots: ["age"],
          today_plan: {},
          latest_checkin: null,
          recent_memories: [],
          progress: {},
          coach_suggestions: [],
        }),
      });
      return;
    }
    if (path === "/v1/usage/summary") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          event_date: "2026-08-09",
          user_used: 20,
          user_limit: 20,
          global_used: 100,
          global_limit: 500,
          live_calls_available: false,
          fallback_mode: "deterministic_offline",
        }),
      });
      return;
    }
    if (path === "/v1/plans/generate") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ plan_id: "plan-1", status: "active", plan: {}, rationale: "baseline" }),
      });
      return;
    }
    if (path === "/v1/checkins/daily") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "recorded", checkin_id: "checkin-1", auto_adjusted: false }),
      });
      return;
    }
    if (path === "/v1/algorithm/agent-runs") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
      return;
    }
    if (path === "/v1/algorithm/challenges/summary") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          experiment_id: "agent_challenge_v1",
          source: "challenge_eval",
          partition: "test",
          training_eligible: false,
          cases: 120,
          passed: 20,
          pass_rate: 0.1667,
          component_scores: { clarification: 0.4667, primary_intent: 0.6917 },
          categories: [],
          failure_count: 100,
          failure_examples: [{ case_id: "challenge-1", category: "ambiguous_reference", checks: {}, expected: {}, actual: {}, user_message: "就按上次那个继续" }],
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
}

test("public demo covers registration, cookie session UI, chat, plan, check-in and algorithm evidence", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");

  await expect(page.getByText("医疗边界", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "注册" }).first().click();
  await page.getByPlaceholder("邮箱").fill("candidate@example.com");
  await page.getByPlaceholder("显示名称").fill("候选人");
  await page.getByPlaceholder("邀请码").fill("interview-demo");
  await page.getByPlaceholder("密码").fill("safe-password-123");
  await page.getByRole("button", { name: "创建账号" }).click();

  await expect(page.getByText("今日在线模型额度已用完", { exact: false })).toBeVisible();
  const composer = page.getByPlaceholder("告诉教练你的目标、今天的状态，或提出问题……");
  await composer.fill("我今天很疲劳，应该怎么练？");
  await page.locator(".send-btn").click();
  await expect(page.getByText("先从可执行的小目标开始。")).toBeVisible();

  await page.getByRole("button", { name: "概览" }).click();
  await page.getByRole("button", { name: "生成计划" }).click();

  await page.getByRole("button", { name: "打卡" }).click();
  await page.getByRole("button", { name: "提交打卡" }).click();
  await expect(page.getByRole("button", { name: "已记录" })).toBeVisible();

  await page.getByRole("button", { name: "算法实验" }).click();
  await expect(page.getByText("20/120")).toBeVisible();
  await expect(page.getByText("就按上次那个继续")).toBeVisible();
  await expect(page.getByText("完成一次聊天后", { exact: false })).toBeVisible();
});
