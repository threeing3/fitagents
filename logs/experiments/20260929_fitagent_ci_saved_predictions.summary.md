# 2026-09-29 录制预测输入测试的 CI 修复

## 动机与边界

GitHub CI 的 Python 3.12 后端测试在 `test_intent_recorded_business_replay.py` 中有 4 项失败：测试直接读取 `logs/experiments/intent_size_compare_20260928/{4b,14b}_full.jsonl`，而原始推理日志按 `.gitignore` 留在本机。干净检出没有这些文件。此次只修复测试输入来源，不改动真实模型报告、业务回放算法或历史实验结论。

## 修改与步骤

1. 测试中根据已提交的固定诊断题集，在临时目录生成两组符合输入契约的**合成**预测记录。
2. 使用这些记录验证案例覆盖、模型来源错配、缺失预测和已有输出目录拒绝覆盖。
3. 真实模型预测文件仍留在本地，不上传到公开仓库；本测试通过不等于再次运行了 4B 或 14B 模型。

## 执行命令与结果

- 原 CI 命令：`python -m pytest tests/ -v --tb=short --timeout=30 --cov=fast_api/app --cov=algorithm --cov-report=term-missing --cov-report=xml --cov-fail-under=65`。
- 原结果：Python 3.12 为 4 failed、969 passed、3 skipped；失败均为缺少未跟踪的本地推理日志。
- 本地目标测试命令：`.venv/Scripts/python -m pytest tests/algorithm/test_intent_recorded_business_replay.py -q --tb=short`，结果 5 passed。
- 本地全量测试命令：`.venv/Scripts/python -m pytest tests/ -q --tb=short --timeout=30 --cov=fast_api/app --cov=algorithm --cov-report=term --cov-fail-under=65`，结果 982 passed、2 skipped，覆盖率 76.44%。
- `ruff check`、`ruff format --check` 和 `mypy` 均通过。干净的 GitHub CI 结果需在推送后确认。

## 风险与下一步

合成测试只覆盖输入契约，不验证历史真实模型记录的真实性或指标。需要评估那些记录时，仍应在持有原始日志的环境中单独运行并保存证据。
