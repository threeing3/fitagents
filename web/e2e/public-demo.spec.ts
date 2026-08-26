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
    if (path === "/v1/algorithm/intent-evaluation/summary") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "fitagent-public-intent-evaluation/v1",
          dataset: { name: "agent_challenge_v1", cases: 120, partition: "test", source: "challenge_eval", training_eligible: false, user_messages_exposed: false },
          paths: [
            { id: "rule_only", label: "Rules v2", role: "safety_baseline", exact_pass_rate: 0.1667, risk_score: 0.8333, model_calls: 0, latency_p50_ms: 0, latency_p95_ms: 0 },
            { id: "qwen3_adapter", label: "Qwen3-4B QLoRA", role: "local_adapter_candidate", exact_pass_rate: 0.1333, risk_score: 1, model_calls: 120, latency_p50_ms: 3210.97, latency_p95_ms: 3630.41 },
          ],
          adapter_delta_vs_base: 0.075,
          observations: [],
          limitations: ["Offline fixed-test evidence only."],
          failure_taxonomy: {
            transitions: { deepseek_all: { rescued_from_rule: 22, regressed_from_rule: 3 }, hybrid: { rescued_from_rule: 12, regressed_from_rule: 0 } },
            categories: [{ category: "multi_intent", cases: 20, paths: { rule_only: { exact_pass_rate: 0, check_scores: {} }, deepseek_all: { exact_pass_rate: 0.3, check_scores: {} }, hybrid: { exact_pass_rate: 0.1, check_scores: {} } }, best_observed_paths: ["deepseek_all"], dominant_deepseek_failure: "secondary_intents", actionability: "diagnostic_only_do_not_tune_on_test" }],
            next_data_contract: { required_partition: "development", must_not_copy_fixed_test_prompts: true, minimum_cases_per_priority_category: 30 },
            limitations: [],
          },
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
  await expect(page.getByRole("heading", { name: "意图算法批量对照" })).toBeVisible();
  await expect(page.getByText("Rules v2")).toBeVisible();
  await expect(page.getByRole("heading", { name: "错误分桶与路径救回" })).toBeVisible();
  await expect(page.getByText("就按上次那个继续")).toBeVisible();
  await expect(page.getByText("完成一次聊天后", { exact: false })).toBeVisible();
});
