# IntentDecisionV2 数据治理

## 目的

意图识别训练数据与固定评测数据必须独立。`tests/evals/agent_challenge_cases.json` 和
`tests/evals/intent_eval_cases.json` 只用于评测，禁止复制、改写后进入训练集。

## 数据层级

每条 `TrainingExample`（统一训练样本）明确记录：

- `source`：数据载体来源，例如规则生成、教师生成或真实 Agent 轨迹。
- `label_source`：标签的实际产生方式。
- `template_family`：共享句式或生成逻辑的一组样本；整个家族只能属于一个集合。
- `teacher_model` 与 `teacher_prompt_version`：教师模型生成标签时必填。
- `human_review_status`：只能是未审核、待审核、通过或拒绝。
- `training_eligible`：只有明确允许训练的训练集或验证集样本才为 `true`。
- `exclusion_reason`：测试样本或被拒绝样本不能训练的原因。

`source=expert_labeled` 只有在真实人工审核通过后才能使用。规则模板数据不得改名为专家数据，
教师模型输出也不得自动视为人工真值。

## 隔离规则

1. 固定评测数据保持 `training_eligible=false`，不能被任何训练构建器读取。
2. 训练构建器采用白名单策略：字段必须显式为 `true`，缺失时按不可训练处理。
3. 同一个 `user_hash` 不能跨集合，同一个 `template_family` 也不能跨集合。
4. 构建时对固定评测问题做标准化后的精确碰撞检查，并在清单中保存评测文件校验和。
5. 当前规则生成语料只用于验证数据管线和初步训练，不足以支持模型质量结论。

## 当前 Intent v1 数据

`intent-v1-20260817` 包含 20 个模板家族、1000 条规则生成样本，四类弱点各 250 条：

- 风险等级判断；
- 是否需要澄清；
- 多意图优先级；
- 当前输入与长期记忆冲突。

其中训练集 800 条、验证集 100 条、内部测试集 100 条；只有前 900 条允许进入训练。
内部测试集用于开发期验证，120 条挑战集仍是最终独立门禁。当前没有教师生成或人工审核数据。

## 复现命令

```powershell
python -m algorithm.datasets.build_intent_dataset `
  --output algorithm/datasets/manifests/intent_v1_20260817.jsonl `
  --manifest algorithm/datasets/manifests/intent_v1_20260817.summary.json `
  --eval tests/evals/agent_challenge_cases.json `
  --eval tests/evals/intent_eval_cases.json `
  --per-family 50
```

原始 JSONL 不进入 Git；只提交生成代码和脱敏清单摘要。
