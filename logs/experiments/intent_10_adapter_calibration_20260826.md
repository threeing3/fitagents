# Intent adapter field calibration log

## 实验动机

上一阶段只验证了字段级路由控制逻辑，尚未用真实 Qwen3-4B Adapter 输出校准主意图和次意图置信度。本轮只使用独立 development 分区，不读取冻结 test 文本，不做正式发布结论。

## 冻结配置

- 实验：`intent_qwen3_4b_20260817`
- 任务：`intent-field-calibration-v1`
- 运行：`field-calibration-dev-seed42-v1`
- 实例：北京 B 区 297 机，`2fa646b3c3-9b548d76`
- GPU：RTX 4090 单卡，禁止无卡模式
- 数据：`intent_dev_v1`，90 条，`training_eligible=false`
- 随机种子：42
- 直接费用上限：8 元
- 计划版本：5
- 代码基线：Git commit `18ebb52`

## 生命周期事件

- 2026-08-26 11:33 +08:00 `instance-start-requested`：控制台观测为已关机，系统盘 3.06%，数据盘 18.38%，健康正常；为真实模型推理校准申请启动，预计 90 分钟，费用上限 8 元。

## 执行命令

```text
bash algorithm/inference/run_field_calibration.sh
```

服务凭据在远端进程内随机生成，仅通过环境变量传递，不写入命令、快照或日志。

## 实验步骤

1. 启动 297 机并执行 GPU、磁盘、Python、CUDA 和 tmux 预检。
2. 验证已有 Qwen3 Adapter、发布清单和推理环境。
3. 创建不可覆盖的运行记录与代码快照。
4. 仅在独立开发集执行字段置信度采集和阈值校准。
5. 保存原始运行记录到忽略目录，只提交不含用户文本的聚合报告。
6. 验证完成并刷新日志后关闭实例。

## 当前状态

2026-08-27 再次请求开机，控制台仍返回“需求 1 卡、空闲 0 卡”。未选择克隆或无卡模式，继续完成本地实现后进行最后一次有界重试。

本地首次定向测试为 1 失败、10 通过。阈值校准器在 0.75 时已达到 85.71% 选择性准确率且覆盖率更高，测试却错误断言阈值必须不低于 0.80。根因属于测试期望错误；保持“达到目标准确率后最大化覆盖率”的预注册策略，将断言改为 0.75 和 0.8571。

当前状态：`implementation-validation`

本地实现完成后进行了第三次、也是计划内最后一次开机重试。控制台没有进入运行中，297 机仍显示已关机；此前两次均明确返回需求 1 卡、空闲 0 卡。按照有界重试规则，将远端任务标记为 `blocked_gpu_capacity`，不切换实例、不扩大预算、不声称完成真实校准。

已创建但尚未远端执行的不可覆盖工件：

- 运行：`field-calibration-dev-seed42-v1`
- 快照：`80c20d0ad6088e00b7e9`
- 快照包含 183 个文件
- 校准数据：90 条独立 development 样例

当前状态：`blocked_gpu_capacity`。恢复方式为等待 297 有空闲 GPU，或由用户明确批准把计划修订到另一个实例。

## 计划修订 6：切换至 256 机

用户于 2026-08-27 明确批准使用北京 B 区 256 机。任务实例由 `2fa646b3c3-9b548d76` 修订为 `321d48b5b7-6e37372c`，GPU 类型保持 RTX 4090 单卡，单次任务费用上限仍为 8 元，不扩大磁盘、不删除或覆盖既有资产。

- 事件：`instance-start-requested`
- 新配置：`fitagent-bjb1-4090-256`
- 启动前控制台状态：已关机、GPU 充足、系统盘 35.66%、数据盘 84.64%、健康正常
- 切换原因：原 297 机连续三次无空闲 GPU；用户明确批准替代实例

当前状态：`instance-start-requested`

## 计划修订 7：克隆 297 完整环境

2026-08-27 检查发现 256 机并非 297 机克隆，其数据盘内容属于 `VLX-Seek` 项目，不能用于 FitAgent 意图适配器校准。用户释放一个既有实例槽位后，明确要求重新克隆并清晰命名。

- 源实例：北京 B 区 297 机，`2fa646b3c3-9b548d76`
- 克隆范围：系统盘 + 数据盘；未启用稀疏文件优化
- 候选配置：北京 B 区 841 机、RTX 4090 24GB 单卡、16 核 CPU、120GB 内存
- 计费方式：按量计费，页面显示 `2.18 元/小时`
- 数据盘：免费 50GB，不增加付费扩容
- 计划名称：`FitAgent-Intent-Qwen3-4B-Calib`
- 安全状态：配置页已准备，因最终创建属于产生费用的云资源购买动作，等待用户在 AutoDL 页面亲自点击“创建并开机”

当前状态：`awaiting-user-cloud-purchase`

## 克隆完成与启动前预检

用户已在 AutoDL 页面完成创建。2026-08-27 核验结果如下：

- 新实例：北京 B 区 841 机，`ylgygfuaq4-213d78aa`
- 实例名称：`FitAgent-Intent-Qwen3-4B-Calib`
- 状态：运行中，RTX 4090 24GB 单卡
- 磁盘：系统盘 3.06%，数据盘 18.38%，与 297 源实例一致
- GPU 预检：NVIDIA GeForce RTX 4090，24564 MiB，总显存占用 0 MiB
- 适配器：`full-800-seed42-v1/outputs/adapter/adapter_model.safetensors` 存在
- 代码状态：远端现有快照早于本地提交 `f718ea9`，必须同步到新的不可覆盖目录后才能运行字段校准

计划版本升为 7，运行实例改为 841 机；指标、90 条 development 数据、随机种子和 8 元任务预算保持不变。

当前状态：`remote-preflight-passed`

## 失败运行：field-calibration-dev-seed42-v1

- 启动时间：2026-08-27 23:00 +08:00
- 代码提交：`f718ea9`
- 结果：前 16/90 条完成，第 17 条服务返回 HTTP 422，运行退出码为 1
- 首个错误边界：模型服务已成功加载，输入请求通过接口校验，但 Qwen3-4B Adapter 在第 17 条生成了无效意图 JSON
- 失败分类：`evaluation-implementation-failure`
- 根因：评测器对单条无效结构化输出调用 `raise_for_status()`，导致整个开发集提前终止；无效模型输出本应作为可度量失败并触发确定性规则回退
- 修复策略：记录 `model_valid=false`、字段置信度 0、字段判错、错误类型与规则回退来源，并继续处理剩余样例
- 不变项：数据集、指标、模型、Adapter、随机种子和安全规则均不改变

当前状态：`diagnosed-awaiting-v2`

## 同步与启动失败记录

### field-calibration-dev-seed42-v2

- 结果：仍在第 17/90 条以相同 HTTP 422 退出
- 根因：国内节点执行 `git fetch` 时尚未完成，后续 `checkout` 被提前输入；远端快照 `HEAD` 实际仍为 `f718ea9`
- 证据：远端 `git rev-parse HEAD` 返回 `f718ea99166a326fce649bfe15a50fd8b13e6377`，源码不存在 `model_valid_rate`
- 分类：`infrastructure-sync-ordering-failure`

### field-calibration-dev-seed42-v3

- 同步方式：本地提交 `4296b5b489b9c353e5199b57d96812cd96fdd9c3` 通过 `git archive` 上传到新目录
- 源码门禁：远端已检出 `model_valid_rate` 标记
- 结果：启动后立即以退出码 2 失败，没有执行模型推理
- 根因：ZIP 中 Shell 脚本保留 Windows CRLF 行尾，Linux Bash 无法解析 `set -euo pipefail`
- 分类：`infrastructure-line-ending-failure`
- 下一步：保持快照不变，v4 命令在执行时去除回车后通过 Bash 管道运行脚本

当前状态：`diagnosed-awaiting-v4`

## 完成运行：field-calibration-dev-seed42-v4

- 运行时间：2026-08-27 23:26:11 至 23:33:43 +08:00，约 7 分 33 秒
- 技术状态：`completed`，退出码 0
- 数据：90 条独立 development 样例；未使用冻结 test 进行调参
- 模型：Qwen3-4B + `full-800-seed42-v1` Adapter
- 置信度方法：`generated_token_probability_v1`
- 模型结构合法率：0.8556
- 原始模型风险下限保持率：0.8000
- 延迟：P50 3854.91 ms，P95 13446.31 ms
- 主意图：所选阈值 0.95，覆盖率 0.5333，接受样本准确率 0.7708，未达到 0.85 目标
- 次意图：所选阈值 0.95，覆盖率 0.6556，接受样本准确率 0.6441，未达到 0.85 目标
- 无效结构化输出：第 17、29、30 条等样例被记录为 `model_valid=false` 并继续运行，不再中断实验
- 安全结论：当前 Adapter 不能直接接管意图决策；确定性规则必须继续承担风险下限和失败回退
- 校准结论：当前 token 概率不能作为可靠的单一字段路由信号，需要增加约束解码、独立校准器或字段级分类头的对照实验

远端完整工件位于：

`/root/autodl-tmp/research/fitagent/experiments/intent_qwen3_4b_20260817/runs/field-calibration-dev-seed42-v4/`

其中包含 `run.log`、`events.jsonl`、`resource_usage.jsonl`、`status.json`、`run_summary.json`、`output_manifest.json`、`calibration_records.jsonl` 和 `calibration_summary.json`。浏览器下载结果包未成功触发，本地尚未同步原始工件；远端工件保持不变且未删除。

当前状态：`completed-technical-awaiting-local-artifact-sync`
