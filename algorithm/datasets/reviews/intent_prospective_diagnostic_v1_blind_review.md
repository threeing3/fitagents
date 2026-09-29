# 意图前瞻诊断 V1：第二位 AI 审阅者盲标

本文件是第二个 AI 审阅者的独立判断，不是人类独立标注，也不是模型运行结果。标注前仅看题面、有效意图名称及任务优先级；未看原金标或相关诊断文档、测试、日志。`secondary_intents` 列列出主意图之外所有明确且独立的行动意图；不把作为背景的运动／饮食事实、明确否定的行动或单纯引用的症状另列为意图。`needs_clarification` 表示执行所要求的动作前需向用户追问，明确要求“先问我”也记为是。风险采用低／中／高三级，针对当前用户请求本身；“高”表示当前健康危险信号，不代表临床分诊结论。

| case_id | expected_primary_intent | secondary_intents | risk_level | needs_clarification | 简短理由与不确定性 |
| --- | --- | --- | --- | --- | --- |
| intent-prosp-001 | `memory_query` | [] | 低 | 否 | 用户说明跑步已入日志，只查配速；日志是否真的有配速需检索后确认，无须先追问。 |
| intent-prosp-002 | `training_log` | [] | 低 | 否 | 明确指出尚未记录并要求补录三公里跑步；配速缺失不妨碍先记录已知字段。 |
| intent-prosp-003 | `general_chat` | [] | 低 | 否 | 明确拒绝保存和查询，只是分享训练事实；也可争议为 `small_talk`，但无寒暄特征。 |
| intent-prosp-004 | `training_log` | [`memory_query`] | 低 | 否 | 保存今天的俯卧撑并查询昨天数量；记录任务按给定优先级在先。 |
| intent-prosp-005 | `training_log` | [] | 低 | 否 | 只要求记录昨天跳绳；“别给计划”是否定，不另列计划意图。 |
| intent-prosp-006 | `training_log` | [`training_plan`] | 低 | 否 | 同时要求补录跳绳和制定新计划；记录任务按优先级在先。计划细节可读取档案，题面未明确要求先追问。 |
| intent-prosp-007 | `concept_explanation` | [] | 低 | 否 | 只问术语含义，明确不修改计划；“计划里”是语境。 |
| intent-prosp-008 | `memory_query` | [] | 低 | 否 | 查询最近跑步时长，不要求变更计划。 |
| intent-prosp-009 | `injury_or_risk` | [] | 高 | 否 | 训练中突发喘不上气且询问处理方式；应立即按风险信号回应，不以追问阻断初步安全回应。 |
| intent-prosp-010 | `memory_query` | [] | 低 | 否 | 明确排除自己的呼吸症状，只查个人跑步距离；旁人症状是否仍应触发安全提醒有争议，但不构成用户自身风险意图。 |
| intent-prosp-011 | `training_plan` | [] | 低 | 否 | 症状词明确是歌词引用，实际请求安排明天慢跑；安排细节可依既有档案，引用不作为风险意图。 |
| intent-prosp-012 | `injury_or_risk` | [] | 高 | 否 | 当前胸口发紧并询问如何处理；“慢跑先别安排”是否定计划动作。 |
| intent-prosp-013 | `profile_correction` | [] | 低 | 否 | 明确指旧体重录入错误，要求更正为当时真实值。 |
| intent-prosp-014 | `profile_update` | [] | 低 | 否 | 旧值当时正确，新测量值更新档案，是状态变化而非纠错。 |
| intent-prosp-015 | `profile_correction` | [] | 低 | 否 | 旧目标为录入错误，目标始终是增肌；应修正历史事实。 |
| intent-prosp-016 | `profile_update` | [] | 低 | 否 | 从今天起改变目标，属于当前档案更新。 |
| intent-prosp-017 | `nutrition_log` | [`memory_query`] | 低 | 否 | 记录午餐并查询上次游泳时长；按优先级先记录。份量不精确，可保存用户原话。 |
| intent-prosp-018 | `training_log` | [`nutrition_advice`] | 低 | 否 | 记录今晚划船机，再询问早餐补蛋白；无当前危险信号。 |
| intent-prosp-019 | `monthly_review` | [`training_plan`] | 低 | 否 | 回顾“本月”运动频率并安排下周练肩；月度回顾按优先级在计划之前。“下周练肩”是否更像进阶决策需仲裁。 |
| intent-prosp-020 | `injury_or_risk` | [`nutrition_log`] | 高 | 是 | 当前呼吸困难优先处理，同时明确要求记录昨晚晚餐，但未提供晚餐内容，完成记录需追问；明确拒绝今天的计划。追问不可延误风险回应。 |
| intent-prosp-021 | `training_log` | [] | 低 | 是 | 用户要求记录卧推且明确说组数尚未给出、先问；可否先保存部分事实是执行策略争议。 |
| intent-prosp-022 | `profile_update` | [] | 低 | 是 | 要更新今日体重，但数字稍后提供，缺少核心值。 |
| intent-prosp-023 | `memory_query` | [] | 低 | 是 | 要查前一次训练并明确要求先问是哪项；“前一次”原可由时间排序确定，但用户指定先追问。 |
| intent-prosp-024 | `training_plan` | [] | 低 | 是 | 计划所需训练天数和目标缺失，用户明确要求先问清。 |
| intent-prosp-025 | `weekly_review` | [] | 低 | 否 | 请求本周完成次数、总时长及变化的整体复盘。 |
| intent-prosp-026 | `memory_query` | [] | 低 | 否 | 只查周三瑜伽时长，明确拒绝周报。若历史有多个周三，检索结果可能需要再澄清。 |
| intent-prosp-027 | `monthly_review` | [] | 低 | 否 | 请求八月训练趋势和频率变化，属于指定月份的整体回顾。若跨年有多个八月，执行时再澄清。 |
| intent-prosp-028 | `memory_query` | [] | 低 | 否 | 只查上月最后一次训练日期，明确拒绝月报。 |
| intent-prosp-029 | `training_log` | [] | 低 | 否 | 口语化的“记一笔”仍是明确记录昨晚半小时单车。 |
| intent-prosp-030 | `general_chat` | [] | 低 | 否 | 明确说“别记”且没有其他任务，只分享昨晚运动；与 `small_talk` 的边界需仲裁。 |
| intent-prosp-031 | `memory_query` | [] | 低 | 否 | “记了多少公里”“就查一下”明确查询既有晨跑记录；若多条晨跑，检索后可能需追问。 |
| intent-prosp-032 | `nutrition_log` | [`training_plan`] | 低 | 否 | 记录今早两个鸡蛋，同时询问明晚练什么；后者按一般训练安排归入计划，也可能归入 `progression_decision`。 |

## 建议仲裁

- `intent-prosp-003`、`intent-prosp-030`：无执行任务的运动陈述属于 `general_chat` 还是 `small_talk`。
- `intent-prosp-010`：旁人急性呼吸困难是否改变用户请求的风险等级或添加 `injury_or_risk`。
- `intent-prosp-019`、`intent-prosp-032`：短期“练什么／怎么安排”是 `training_plan` 还是 `progression_decision`。
- `intent-prosp-020`：风险处理与缺少晚餐内容的记录并存时，`needs_clarification` 的定义是否按所有任务判定。
- `intent-prosp-021`、`intent-prosp-023`：可部分记录／可直接检索，但用户明确要求先问时，是否统一标为需澄清。
- `intent-prosp-026`、`intent-prosp-027`、`intent-prosp-031`：时间指称可能对应多条历史记录；本盲标默认先检索，只有实际歧义才追问。

## 实际看过的输入

1. 工作区的 `AGENTS.md`：项目工作规则。
2. `algorithm/inference/intent_catalog.py`：有效意图名称集合。
3. `fast_api/app/services/intent_decision.py`：仅以 `TASK_PRIORITY` 为目标检索的输出，其中命令的上下文还显示了相邻的风险词表开头及后面一次同名引用和任务动作说明；这些附带行未用于读取题库答案。
4. `algorithm/datasets/fixtures/intent_prospective_diagnostic_v1.json`：只通过 `Get-Content -LiteralPath algorithm/datasets/fixtures/intent_prospective_diagnostic_v1.json -Raw | ConvertFrom-Json | Select-Object case_id,user_message | ConvertTo-Json -Depth 3` 得到 `case_id` 与 `user_message` 投影；未打开或打印原始文件。

未读取原金标、诊断说明、相关测试、实验日志；未运行意图模型，也未与其他审阅者结果比较。
