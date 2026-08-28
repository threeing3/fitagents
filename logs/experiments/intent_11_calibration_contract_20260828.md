# 意图校准记录契约 v2

## 实验动机

上一轮 90 条开发集校准只记录字段是否正确，没有保存脱敏后的预测标签和解析错误码，无法生成混淆矩阵，也无法判断具体标签之间的混淆模式。本轮先补齐评测证据契约，再执行远程校准；冻结测试集继续不参与调参。

## 修改内容

- 逐条记录保存期望标签和预测标签，但不保存用户原文。
- 记录 `parse_error_code`、`fallback_applied` 和 `retry_count`。
- 将原始模型风险结果与确定性规则回退后的风险结果分开记录。
- 聚合主意图和风险等级混淆矩阵。
- 聚合次意图逐标签 Precision、Recall、F1 和 Macro-F1。
- 聚合澄清准确率、解析错误、回退和重试次数。

## 执行命令

本地验证：

```text
.venv\\Scripts\\python.exe -m pytest tests/algorithm/test_intent_adapter_calibration.py -q
.venv\\Scripts\\python.exe -m ruff check algorithm/evaluation/intent_adapter_calibration.py tests/algorithm/test_intent_adapter_calibration.py
git archive --format=zip --output=fitagent-c0071fa.zip c0071fa
```

远程运行核心命令：

```text
mkdir -p /root/autodl-tmp/research/fitagent/snapshots/c0071fa-uploaded
unzip -q /root/fitagent-c0071fa.zip -d /root/autodl-tmp/research/fitagent/snapshots/c0071fa-uploaded
nohup env RESEARCH_OUTPUT_DIR=<v5 outputs> bash -lc "tr -d '\\r' < algorithm/inference/run_field_calibration.sh | bash" > <v5 run.log> 2>&1 &
```

## 实验步骤

1. 修改本地评测记录契约并增加单元测试。
2. 运行静态检查和相关测试。
3. 同步代码到 AutoDL 841 机。
4. 使用独立的新 `run_id` 执行 90 条开发集校准。
5. 下载、校验和分析工件。

## 实验结果

- 运行 ID：`field-calibration-dev-seed42-v5-contract-v2`
- 代码提交：`c0071fa`
- 代码快照 SHA-256：`1469B0A42D4B868C8D2BF98D28D812A204370746509E7B5A4CC0B3EA73F773A6`
- 样例：90/90 完成，进程正常退出
- 结构合法率：85.56%
- 主意图准确率：54.44%
- 次意图 Macro-F1：0.1998
- 澄清准确率：34.44%
- 原始风险下限保持率：75.56%
- 高风险精确命中：11/30；另有 6 条降为中风险、3 条降为低风险、10 条结构无效
- 回退：13；重试：0
- 延迟 P50/P95：3895.23/13703.1 ms
- 公开聚合报告：`algorithm/evaluation/reports/intent_field_calibration_v5_contract_v2_summary.json`

完整远程工件保存在：

`/root/autodl-tmp/research/fitagent/experiments/intent_qwen3_4b_20260817/runs/field-calibration-dev-seed42-v5-contract-v2/`

## 失败原因或下一步计划

AutoDL 国内节点无法直连 GitHub，改用只含 Git 跟踪文件的本地归档上传；远端与本地 SHA-256 一致。浏览器下载结果包的事件未正确回传，但远程完整工件和 ZIP 均已保留，聚合摘要已通过 Jupyter JSON 查看器逐项核验。

下一步先实现 JSON Schema 约束生成与一次有界修复，然后建立 TF-IDF + Logistic Regression 判别式 CPU 基线。风险字段继续由确定性规则托底，不能由 Adapter 独立决策。
