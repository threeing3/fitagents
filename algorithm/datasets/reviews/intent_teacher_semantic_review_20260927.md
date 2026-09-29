# FitAgent 教师候选语义初审（助手单人，2026-09-27）

## 范围与读取方法

原数据：`algorithm/datasets/generated/intent_multilabel_v3_1_20260922.jsonl`，筛选`source=teacher_generated`，按`example_id`排序逐条读取原文和`assistant_response`的主、次意图。共188条、24个标签组合族。完整编号格式为`intent-multilabel-v3-augment-{下表族名}-{train|validation}-{00|01|02|03}`；唯一例外是`primary-injury_or_risk`只有4条训练候选。下表每一行覆盖该族实际存在的所有候选；“需仲裁”列之外的条目只判为**字面上暂可支持原标签**，绝不是人工批准、独立复核或可训练金标。

审查问题：原句是否请求系统执行所标任务，还是只提及、报告完成事实、自行判断；有无纠正与普通更新混淆、无明确授权的写入、主次任务漏标。安全结论、工具执行许可和现实自然度不在本轮单人初审中裁定。

## 覆盖清单

| 标签组合族 | 实读条数 | 需仲裁的短编号（同族前缀省略） | 主要理由 |
| --- | ---: | --- | --- |
| `multi-injury_or_risk-training_plan` | 8 | 无 | 当前肩痛和计划调整请求均可从字面支持；仍须由安全策略决定能否提供具体计划。 |
| `multi-nutrition_advice-recovery_check` | 8 | 无 | 饮食咨询与恢复状态询问均可从字面支持。 |
| `multi-recovery_check-training_log` | 8 | `train-00..03`, `validation-00..03` | 训练句要求恢复评估，但只是提到跑过步或已记录，不等于请求写入；验证句要求记录跑步，却没有要求系统评估恢复。 |
| `multi-training_log-monthly_review` | 8 | `train-00..03`, `validation-00..03` | 提到既有月复盘、复盘目标或“供月复盘用”，不等于本轮请求系统做月度复盘。 |
| `multi-training_plan-nutrition_advice` | 8 | `train-00..03` | 想增肌或打算系统训练不等于要求生成训练计划；训练句主要在询问饮食，验证句才明确请求两项。 |
| `multi-training_plan-profile_correction` | 8 | 无 | 均表达纠正先前器械事实并要求调整计划。 |
| `multi-training_plan-profile_update` | 8 | `validation-00` | “不按原来的三天安排练”可能是纠正旧设置；是否要求系统重排计划不够明确。 |
| `multi-weekly_review-progression_decision` | 8 | `train-00..03`, `validation-02` | 训练句多为用户自己复盘并已作加重判断，不一定请求系统复盘或决策；验证02句“我得理一理”缺明确对系统的请求。 |
| `primary-concept_explanation` | 8 | 无 | 均询问训练术语含义。 |
| `primary-general_chat` | 8 | 无 | 均明确闲聊，无业务动作请求。 |
| `primary-injury_or_risk` | 4 | 无 | 均报告硬拉后腿麻与无力并询问风险；实际安全处置需另外评审。 |
| `primary-memory_query` | 8 | 无 | 均问过去记录，不是在报告当前受伤。 |
| `primary-monthly_review` | 8 | 无 | 均要求对比本月和上月表现。 |
| `primary-nutrition_advice` | 8 | 无 | 均询问增肌期饮食/蛋白分配。 |
| `primary-nutrition_log` | 8 | `train-00`, `train-02` | “早餐，两个鸡蛋”“两个鸡蛋，早饭”缺已食用事实或明确记录请求；训练01/03有“记”。 |
| `primary-onboarding` | 8 | `validation-01`, `validation-03` | “一开始得先弄一下吧”与“初始选项还没动过”可为感叹/状态报告，尚不足以认定授权执行初始设置。 |
| `primary-profile_correction` | 8 | 无 | 均给出原体重错误与新值，有纠正语义。 |
| `primary-profile_update` | 8 | `train-00`, `train-02`, `train-03` | 训练00/03说旧器械信息错误，更像纠正；训练02只是陈述拥有哑铃，缺修改资料请求。 |
| `primary-progression_decision` | 8 | 无 | 均询问卧推是否加重。 |
| `primary-recovery_check` | 8 | 无 | 均就静息心率升高询问训练减量/恢复判断；不能据此推定医学安全。 |
| `primary-small_talk` | 8 | 无 | 均为问候。 |
| `primary-training_log` | 8 | 无 | 均明确要求记录已做卧推五组。 |
| `primary-training_plan` | 8 | 无 | 均要求调整当前计划。 |
| `primary-weekly_review` | 8 | `train-00`, `train-01`, `train-03` | 00质问系统是否关注，01像对系统说“你没练”；03只是对完成情况的猜测性问句，均需确认是否真要求读取/复盘本人一周记录。 |

共36条进入需仲裁清单，152条仅为单人初审“字面上暂可支持”。36/188不是总体标签错误率，也不是独立抽样估计；本次逐条读过全部文本，但相似文本集中，判断可能存在系统性偏差。

## 对训练与评测的直接影响

最重要的错误边界是**提及 ≠ 请求执行**。若把上述候选直接训练，模型可能把“跑了步”变成训练写入、把“月复盘里”变成重新生成月复盘、把“我觉得别加重”变成系统加重决策。另一个边界是“资料原值错误”与“资料自然更新”不能共用同一持久化语义。

这些结论只产生仲裁优先级：先按族处理36条，明确事实/请求/禁止/对象/时态及必要槽位，再由另一位审核者独立复核或由项目负责人仲裁。不要把其余152条自动改为`approved`；仍需覆盖全部候选的真实审阅记录、按场景母句分组切分和独立固定测试。
