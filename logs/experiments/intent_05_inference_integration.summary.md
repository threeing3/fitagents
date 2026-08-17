# Intent 05 inference integration

## 实验动机

将意图识别从“规则 + DeepSeek”升级为可替换的三层运行链路：确定性安全规则、Qwen3-4B QLoRA 适配器、DeepSeek 降级路径，并让 Agent Lab 如实展示实际路由来源。

## 修改内容

- 新增有超时、鉴权和结构校验的适配器推理客户端。
- 适配器成功时优先使用；不可用或失败时才进入原 DeepSeek 精炼流程。
- 任何模型结果都继续经过规则安全覆盖。
- 新增登录后单例意图对比接口和 Agent Lab 展示；未完成真实训练时固定显示未配置或未验证。

## 执行命令与步骤

1. `python -m pytest tests/test_intent_decision_engine.py tests/test_product_security.py -q`
2. `python -m pytest -q`
3. `npm run typecheck`、`npm test`、`npm run build`

## 实验结果

- 定向后端测试：16 passed。
- 全量后端测试：603 passed，保留 2 条 Starlette 第三方弃用警告。
- 前端类型检查：通过。
- 前端组件测试：1 passed。
- 前端生产构建：通过，主 JavaScript 产物约 196.83 kB（gzip 64.49 kB）。
- 静态检查首次发现 1 处导入顺序问题，已手工修复并复查。
- 首次远端 CI 发现 2 个 Python 文件未通过格式检查，以及旧端到端模拟响应缺少新增字段导致页面渲染失败；已增加向后兼容判断并执行项目格式化器，随后重跑对应门禁。
- 最初误用系统 Python 且在仓库根目录执行 npm，分别因缺少 pytest 和 package.json 失败；改用 `.venv` 与 `web` 工作目录后通过。这属于命令入口错误，不是实验结果失败。
- CI 修复复现时又从 `web` 目录误调用根目录 `.venv`，Python 格式命令未执行；随后改回仓库根目录独立验证。端到端复现进一步确认旧模拟服务完全没有 `/summary` 路由，因此将可选摘要与核心轨迹请求解耦。

真实 GPU 训练和 Adapter 指标仍不在本次结果范围内。

## 风险与下一步

- AutoDL 未授权启动，因此当前不能声称适配器在线或优于基线。
- 下一步在独立 GPU 实验中产出适配器，通过重载与固定测试集门禁后再配置推理地址。
