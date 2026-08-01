# 数据治理规范

## 数据来源

允许的数据来源包括：`agent_trace`、`rule_generated`、`expert_labeled`、`teacher_generated`、`synthetic`、`simulated_outcome` 和 `seed_eval`。

每条数据必须包含来源、Schema 版本、模型版本、Prompt 版本、规则版本和数据切分。

## 脱敏要求

- 用户 ID、会话 ID只保存不可逆哈希，不保存明文标识。
- 邮箱、手机号、API Key 和 Token 必须在导出前替换。
- 健康、用药、伤病信息按敏感字段处理。
- 日志中的原始请求内容只能进入授权的训练导出流程。
- 不把 `.env`、数据库密码或生产连接信息导入数据集。

## 切分规则

训练、验证和测试必须按用户哈希和场景分组切分。相同用户不能同时出现在训练集和测试集；相同场景模板也不能简单复制到多个切分。

## 质量门禁

- JSONL 每行必须是对象。
- `example_id` 不得重复。
- `source` 和 `split` 必须是受控枚举。
- 用户消息不能为空。
- 偏好对的 chosen 和 rejected 必须不同。
- 数据集生成必须产出 manifest 和 validation report。
- 任何被隔离的样本进入 `quarantine`，不得静默丢弃。
