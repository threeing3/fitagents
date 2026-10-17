# 2026-10-18 代码同步记录

## 本次范围

- 持久化会话摘要和上下文检查点，保存来源、版本及压缩事件；保留原始聊天。
- 最近完整消息的预算处理、超窗恢复以及工具结果裁剪边界。
- 语义记忆候选抽取、原文证据校验与意图调用复用。
- 简化为关键词和向量两路召回，共用排名融合规则，保留时效性与适用性。
- 前端执行时间线补充上下文压缩事件名称，同步项目与架构说明。

## 提交与数据边界

同步到独立功能分支，不合并主分支，也不触发原演示分支部署。
不上传本地账户、聊天数据库、备份、环境变量、原始日志或个人学习材料。
本次没有执行数据库迁移、调用收费模型或修改云端配置。

迁移 019 的 PostgreSQL（关系数据库）在线升级需要经过备份验证的入口，
不能把本次代码推送理解为已完成部署升级。历史迁移 016 的字段对齐也包含在
本次待审查代码中；已经执行过该历史版本的数据库不会因为文件变化自动补齐。
部署前应独立核对实际结构和迁移版本，并制定针对该环境的升级方案。

## 本机验证

- 34 个变更 Python（后端语言）文件的 Ruff（代码规范与格式检查器）检查及格式检查通过。
- Mypy（静态类型检查器）通过。
- 本次变更涉及的测试文件共 134 项后端测试，在 30 秒单项时限下全部通过。
- 前端类型检查、93 项组件测试、18 项页面流程测试和生产构建通过。
- 前后端依赖审计未发现已知漏洞。
- OpenAPI（接口描述规范）可生成并序列化，包含 82 条路径。
- 暂存路径检查及高置信度密钥模式扫描通过；此扫描不替代完整密钥审计。
- 后端全量测试的首次标准命令触发 30 秒子进程恢复测试超时，保留失败记录；
  延长本机时限后的复查结果单独记录，不将复查等同于标准时限通过。

延长单项时限到 120 秒后，全量结果为 **1681 通过、2 跳过、3 失败**，
耗时 448.02 秒，覆盖率 80.83%（达到 65% 覆盖率门槛，但测试整体未通过）。
崩溃恢复用例在此次复查中通过，剩余失败均在 `tests/test_business_state_replay.py`：

- `test_untrusted_retrieved_memory_cannot_authorize_model_plan_write`：预期不可信记忆进入上下文后验证权限边界，但该文本没有进入上下文，断言提前失败；不能据此声称发生了越权写入。
- `test_same_day_checkin_versions_memory_and_excludes_old_state_from_context`：纠正后的当前记忆没有出现在上下文候选中。
- `test_joint_checkin_correction_adjustment_and_retry_has_one_final_state`：联合链路中同样未召回当前记忆。

下一步应检查检索简化后的相关性门槛、当天状态上下文通道与这些测试的数据契约，
区分检索回归和旧测试假设，不以放宽断言掩盖问题。本次只归档，不宣称可合并发布。

本机仅验证 Python 3.11，未复现 3.12 环境；使用现有前端依赖，未重新安装。
Docker（容器运行环境）服务未启动，未验证容器构建；未运行 Gitleaks（密钥扫描工具）。
功能分支推送本身不触发当前仓库的持续集成流程，不能声称远端检查已经通过。

## 过程记录

本次动机是安全归档近期累积实现，而非新增实验或宣称线上收益。
先核对分支和文件，再排除敏感产物，最后执行检查并同步代码。

执行的主要检查命令：

```text
python -m ruff check <changed-python-files>
python -m ruff format --check <changed-python-files>
python -m mypy
python -m pytest <changed-test-files> -q --tb=short --timeout=30
python -m pytest tests/ -v --tb=short --timeout=30 --cov=fast_api/app --cov=algorithm --cov-report=term-missing --cov-report=xml --cov-fail-under=65
python -m pytest tests/ -q --tb=short --timeout=120 --cov=fast_api/app --cov=algorithm --cov-report=term --cov-report=xml --cov-fail-under=65
python -m pip_audit --strict --requirement fast_api/requirements.txt
npm run typecheck
npm test
npm run test:e2e
npm run build
npm audit --audit-level=high --registry=https://registry.npmjs.org
```

原始后端测试输出保存在本机 `logs/github-sync-20261018-pytest.log` 和
`logs/github-sync-20261018-pytest-retry.log`；依赖审计输出保存到同目录的
`github-sync-20261018-dependency-audit.log`。这些文件不随本次提交上传。
变更相关测试的输出保存在 `logs/github-sync-20261018-changed-tests.log`。
