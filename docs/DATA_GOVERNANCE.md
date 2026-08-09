# 数据治理规范

## 数据来源

允许的数据来源包括：`agent_trace`、`rule_generated`、`expert_labeled`、`teacher_generated`、`synthetic`、`simulated_outcome` 和 `seed_eval`。

每条数据必须包含来源、Schema 版本、模型版本、Prompt 版本、规则版本和数据切分。

`feedback_id` 是可选字段；只有标签来源为用户反馈时才必须填写。字段不存在表示“该样本不是由可追踪的用户反馈直接生成”，不能把缺失值自动解释为正向或负向反馈。

`expert_labeled` 只允许用于 `quality_labels.review_status=approved` 的真实人工审核样本。规则、教师和合成数据即使质量较高，也必须保留原来源，不能改写为专家标注。

## 脱敏要求

- 用户 ID、会话 ID只保存不可逆哈希，不保存明文标识。
- 邮箱、手机号、API Key 和 Token 必须在导出前替换。
- 健康、用药、伤病信息按敏感字段处理。
- 日志中的原始请求内容只能进入授权的训练导出流程。
- 不把 `.env`、数据库密码或生产连接信息导入数据集。

## 切分规则

训练、验证和测试默认按用户哈希分组切分；`scenario`（场景）只用于统计和分层分析，不能参与同一用户的切分哈希。相同用户不能同时出现在训练集和测试集；用户数不少于 3 但某个集合因小样本哈希为空时，允许把一个确定性用户组移入空集合，并在 manifest 中保留该事实。需要评估模板泛化时，另行建立场景留出实验并记录独立 `experiment_id`，不能把普通用户切分结果包装成场景外推结论。

用户数不少于 10 时，切分器按用户哈希确定性排序，再以整用户为单位分配到最接近 80/10/10 的训练、验证和测试集合。固定评测数据使用 `source=seed_eval`、`partition=test`，并强制 `training_eligible=false`；任何训练构建器都不得读取这些样本。

## 质量门禁

- JSONL 每行必须是对象。
- `example_id` 不得重复。
- `source` 和 `split` 必须是受控枚举。
- 用户消息不能为空。
- 偏好对的 chosen 和 rejected 必须不同。
- 数据集生成必须产出 manifest 和 validation report。
- 任何被隔离的样本进入 `quarantine`，不得静默丢弃。
- Manifest 必须同时报告来源、切分、场景、训练资格和用户泄漏统计。
- DPO 数据必须至少包含 150 对真实人工审核且 `review_status=approved` 的偏好对；合成偏好不能满足该门禁。

## 阶段三数据基线

`maturity_03_algorithms_20260809` 生成 1200 条显式标记为 `synthetic` 的样本，共 50 个用户和 6 个场景。整用户切分为 960/120/120，Schema 合法 1200/1200，用户跨集合泄漏为 0，进入训练的 `seed_eval` 为 0。安全子集 200 条，工具决策 1200 条；真实人工审核偏好对为 0，因此 DPO 关闭。

上述数字只证明数据工厂和隔离机制可复现，不代表真实用户规模、真实接受率或线上业务提升。公开清单位于 `algorithm/datasets/manifests/maturity_03_synthetic.summary.json`；包含样本文本的 JSONL 仅保留在被忽略的实验目录中。
