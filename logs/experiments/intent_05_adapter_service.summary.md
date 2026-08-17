# Intent 05 adapter inference service

## 实验动机

补齐主应用意图客户端对应的真实服务端，使训练后的 Qwen3-4B Adapter 能以独立、受鉴权、可探活的进程接入 Agent 决策链。

## 修改内容

- 新增独立意图推理服务，启动时加载 Qwen3-4B 与 PEFT Adapter。
- 使用 4-bit NF4 推理、确定性生成和与训练一致的聊天模板。
- 新增存活/就绪检查、Bearer 鉴权、请求长度限制和严格 JSON 解析。
- 服务无密钥、无 CUDA 或 Adapter 不完整时拒绝启动。
- 增加隔离推理依赖和部署说明。
- 增加单 GPU 生成锁，避免并发生成争抢显存；只投影允许的档案/规则字段，并限制结构化上下文长度。

## 执行命令与步骤

1. `python -m pytest tests/algorithm/test_intent_inference_service.py -q`
2. `python -m ruff check algorithm/inference tests/algorithm/test_intent_inference_service.py`
3. `python -m ruff format --check algorithm/inference tests/algorithm/test_intent_inference_service.py`
4. `python -m pytest -q`

## 实验结果

- 服务定向测试：2 passed；与主应用意图引擎联合定向验证共 6 passed。
- 全量后端测试：605 passed，保留 2 条 Starlette 第三方弃用警告。
- Ruff 静态检查与格式检查：通过。
- 前端类型检查、组件测试和生产构建：通过；本轮未改前端行为。

本轮只验证服务契约，不加载真实模型，不形成效果结论。

## 风险与下一步

- 真实 Adapter 尚未训练，因此服务的 GPU 加载、显存占用、吞吐和延迟仍待 AutoDL 实测。
- 必须先通过 fresh-process Adapter reload 门禁，再允许配置主应用的推理地址。
- 两个既有 AutoDL SSH 别名的只读预检均失败：北京节点要求交互密码，重庆节点关闭连接；未执行实例生命周期操作。
