# 意图结构校验与一次有界修复

## 实验动机

v5 开发集诊断中 13/90 条输出结构无效，无效输出同时具有更高尾延迟。目标是在不改变数据切分和 Adapter 的前提下，验证一次短修复能否提高结构合法率，同时严格限制额外延迟和 Token 消耗。

## 修改内容

- 在提示中加入统一的 IntentDecision JSON Schema。
- 首次输出通过严格解析和意图目录校验后直接返回。
- 首次失败时最多执行一次修复，修复上限为 96 个新 Token。
- 第二次失败返回结构化错误码并立即交给确定性规则回退。
- 服务和评测记录累计 Token 数与实际重试次数。

## 执行命令

本地验证：

```text
.venv\\Scripts\\python.exe -m pytest tests/algorithm/test_intent_inference_service.py tests/algorithm/test_intent_adapter_calibration.py -q
.venv\\Scripts\\python.exe -m ruff check algorithm/inference/intent_service.py algorithm/evaluation/intent_adapter_calibration.py tests/algorithm/test_intent_inference_service.py tests/algorithm/test_intent_adapter_calibration.py
.venv\\Scripts\\python.exe -m mypy algorithm/inference/intent_service.py algorithm/evaluation/intent_adapter_calibration.py --ignore-missing-imports
```

## 实验步骤

1. 增加依赖无关的严格解析、目录校验和一次修复控制流。
2. 覆盖首次成功、修复成功、修复失败三条路径。
3. 本地测试通过后建立新的不可变代码快照和 run ID。
4. 在 841 机运行相同的 90 条开发集并与 v5 对照。

## 实验结果

本地控制流验证通过：12 项测试通过，Ruff 与 Mypy 均通过。远程开发集实验待执行。

## 限制与下一步

本轮是 Schema 引导生成与事后严格校验，不是逐 Token 约束解码。只有在验证修复收益与成本后，才决定是否引入专门约束解码依赖。
