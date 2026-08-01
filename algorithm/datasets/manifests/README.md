# Local dataset manifests

本目录只保留构建器的输出位置。`*.jsonl`、校验报告和 manifest 默认被 Git 忽略，因为导出数据可能包含用户输入、健身档案或 Agent 回复。

从本地授权数据重新生成：

```powershell
python -m algorithm.data.export_traces --output algorithm/datasets/manifests/training_examples.jsonl --db local_dev.db --log-dir logs/agent-runs --salt "local-dev-salt"
python -m algorithm.data.validate_dataset algorithm/datasets/manifests/training_examples.jsonl --report algorithm/datasets/manifests/validation.json
python -m algorithm.datasets.build_sft_dataset algorithm/datasets/manifests/training_examples.jsonl algorithm/datasets/manifests/sft_train.jsonl
```

开源仓库只提交数据结构、构建脚本和文档；不要把真实用户数据或未审核的合成数据提交到仓库。
