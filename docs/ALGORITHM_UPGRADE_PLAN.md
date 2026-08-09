# 算法升级与面试项目说明

## 项目定位

本项目现在同时展示两条能力线：

1. 大模型应用算法：意图识别、记忆召回、工具规划、上下文构建、回复重排序和安全评测。
2. 业务算法后训练：从 Agent 轨迹、用户反馈和决策结果构造数据，训练业务回复适配器，并预测推荐接受与训练依从性。

现有 FastAPI/React 产品链路保持不变，新增 `algorithm/` 作为离线实验层。

## 运行顺序

```powershell
# 1. 导出脱敏样本
python -m algorithm.data.export_traces `
  --output algorithm/datasets/manifests/training_examples.jsonl `
  --db local_dev.db `
  --log-dir logs/agent-runs `
  --salt "replace-with-deployment-salt"

# 2. 校验数据
python -m algorithm.data.validate_dataset `
  algorithm/datasets/manifests/training_examples.jsonl `
  --report algorithm/datasets/manifests/validation.json

# 3. 构建 SFT/工具决策数据
python -m algorithm.datasets.build_sft_dataset `
  algorithm/datasets/manifests/training_examples.jsonl `
  algorithm/datasets/manifests/sft_train.jsonl
python -m algorithm.datasets.build_tool_decision_dataset `
  algorithm/datasets/manifests/training_examples.jsonl `
  algorithm/datasets/manifests/tool_decisions.jsonl

# 4. 运行应用算法基线
python -m algorithm.app_algorithms.intent_baseline tests/evals/intent_eval_cases.json

# 5. AutoDL 上安装训练依赖后运行 QLoRA
pip install -r algorithm/training/requirements-training.txt
python -m algorithm.training.sft.train_qlora --config algorithm/training/configs/sft_qwen3b.json
```

## 面试叙事

```text
业务状态与 Agent trace
  -> 数据治理与版本化样本
  -> 应用算法基线与业务标签
  -> SFT/DPO 后训练
  -> 安全门禁与业务重排序
  -> 离线评测和结果追踪
```

真实用户数据、教师模型数据和合成数据必须在 manifest 中分开统计；模拟业务指标不得描述为线上效果。

## 成熟版推进状态

- 阶段一已完成：仓库基础、CI、生命周期、健康检查和生产镜像。
- 阶段二已完成：Cookie 认证兼容、邀请码、演示账号、配额、安全校验和 Algorithm Lab 产品入口。
- 阶段三已完成本地验收：38/38 业务门禁、五类固定评测、真实向量不可用的诚实降级、1200 条合成数据工厂和 DPO 人工审核门禁。
- 阶段四待完成：Qwen3-4B、仅助手回复损失、QLoRA 烟雾训练与正式训练、Adapter 对照评测。
- 阶段五待完成：Render/Neon 动态站、EdgeOne 静态镜像、发布材料与 72 小时观察。

阶段三覆盖率为 65.90%（565 个测试全部通过），满足当前 65% CI 门禁，但尚未达到成熟版最终 70% 目标。后续通过增加训练、适配器推理和发布路径测试补足，不通过排除低覆盖模块修改统计口径。
