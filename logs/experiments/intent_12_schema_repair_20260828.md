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

本地控制流验证通过：12 项测试通过，Ruff 与 Mypy 均通过。

2026-08-29 在 AutoDL 841 机完成 90 条独立开发集真实推理。最终有效运行 ID 为 `field-calibration-dev-seed42-v6-schema-repair-retry2`，快照 ID 为 `8ec5ac2504a435ea4989`。前两次启动分别因后台命令嵌套引号和快照遗漏 `fast_api` 依赖失败，均发生在模型加载前并已保留失败记录。

与 v5 对比：

- 结构合法率：85.56% → 92.22%。
- 主意图准确率：54.44% → 61.11%。
- 次级意图宏平均 F1：19.98% → 13.05%。
- 澄清判断准确率：34.44% → 53.33%。
- 原始风险下限覆盖率：75.56% → 81.11%。
- P50 延迟：3895.23ms → 3777.30ms。
- P95 延迟：13703.10ms → 7667.36ms。
- 触发修复 7 条，修复成功 0 条，最终回退 7 条。

结论：统一 Schema 提示改善了整体生成稳定性和部分字段准确率，但一次事后修复没有救回任何无效样例，并且多意图指标下降。本轮只支持“结构提示有帮助”的诊断结论，不支持发布该 Adapter 或声称多意图问题已经解决。

## 限制与下一步

本轮是 Schema 引导生成与事后严格校验，不是逐 Token 约束解码。下一步不继续叠加相同修复，而是建立 TF-IDF + 逻辑回归判别式基线，并实现带独立次级意图 Sigmoid 输出头的多任务分类模型。
