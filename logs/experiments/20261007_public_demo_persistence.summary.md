# 公共演示持久追踪与部署 — 2026-10-07

## 动机

免费应用托管没有持久磁盘，原流式追踪文件随实例更换可能消失。将脱敏执行事件独立提交到演示数据库，保持相同读取与游标接口，不通过回放重新执行业务。

## 修改

新增 stream_journals 与 stream_journal_events 及 018 迁移；支持 STREAM_JOURNAL_BACKEND=database。默认仍为 file，本地预览兼容只缺新增追踪表的 017 数据库，禁止隐式迁移。
目录记录所有者和会话；事件按数据库锁保护的追加序号保存。每次追加独立事务；无结束事件保持 unconfirmed。未知读者、会话不匹配、游标超前、重复目录身份拒绝或不返回数据。

## 授权与安全

用户允许使用现有 DeepSeek 密钥上传 Render 私密环境变量、全站每日最多 20 次调用，并允许新增迁移用于独立 Neon 演示库。不上传本地账号、聊天、数据库文件；不在本地业务库执行新迁移。费用规格选择免费，关闭自动部署；真正公开服务前再次核对访问边界。

## 执行记录

1. 相关测试首轮 18 passed，追加提交失败回滚检查后 19 passed；涵盖新存储、旧文件存储、分页及本地预览配置。执行 `pytest tests/test_database_stream_journal.py tests/test_durable_stream_journal.py tests/test_stream_event_pages.py tests/test_local_preview.py -q --no-cov --timeout=60`。
2. 新增和相对主分支修改的 Python 文件：Ruff lint 与 format --check 均通过；mypy 返回 0。
3. 前端 typecheck 通过，22 个文件 92 项组件测试通过；生产构建通过；端到端测试 18 passed。前端依赖审计返回 0 vulnerabilities。
4. 按现有 CI 的 `pytest tests/ -v --tb=short --timeout=30 --cov=fast_api/app --cov=algorithm --cov-report=term-missing --cov-report=xml --cov-fail-under=65` 执行，全量检查约 18% 时在 test_background_process_crash 的真实 HTTP 重启等待步骤触发 30 秒超时；未完成覆盖率验收。日志：logs/public_demo_ci_python_20261007.log。不是全量通过。
5. 尝试本机 Docker 构建，Docker Desktop 引擎未运行，构建没有执行成功；未修改 Docker 或系统设置。后续使用 Render 的实际构建结果验证。
6. 使用 scripts/verify_cloud_journal.py 尝试只初始化新建空 Neon 演示库。本机 DNS 将该域名解析为代理虚拟地址 198.18.0.100，连接被关闭；没有建立连接或执行迁移。未修改代理、DNS 或系统网络配置。改用 Render 实例启动时迁移，并以云端实际状态验收。
7. 用户另行确认将新 Neon 连接凭据放入 Render 私密环境变量并公开部署。选择 $0 规格，关闭自动部署；生产环境启用 secure cookie、数据库追踪、持久模型额度（全站每日 20 次）、邀请码注册；共享演示账号暂不启用。
8. Python 依赖安全审计返回 0；Render 对 e59a08e 的实际镜像构建成功并进入部署。复核运行依赖发现 Dockerfile 原先遗漏 algorithm.inference.intent_catalog；补入包入口、子包入口和 intent_catalog.py 三个小文件，不复制训练数据或权重。

9. 首次 e59a08e 部署失败；修复打包后的 20e9c95 在 Render 显示 Deploy succeeded。平台日志证实在独立 Neon 空库执行 001 至 018 迁移，未迁移本地库。实际页面成功打开 https://fitagent-demo.onrender.com/。
10. `scripts.verify_public_demo prepare` 以两个合成账号验收通过：错误邀请码拒绝、注册与真实模型请求、完成状态、12 条持久事件、两次成功模型调用、全站每日额度 20 和跨账号读取 404；当日已用计数 2。记录：logs/public_demo_prepare_20261007.json（无密码或令牌）。
11. 2026-10-07 00:51（北京时间）平台 Events 显示 Service restarted by you；随后 `scripts.verify_public_demo read` 在新进程登录并读取原运行 781725ee-9f9d-4308-8ede-5fa44d925e01，12 条事件、两次成功模型记录和当日调用计数 2 不变，隔离检查通过。没有重发模型请求。
12. 浏览器实际登录合成账号并展开完整执行追踪，显示 69 条合并时间线记录；发现 journal.end 投影未读取 state，误显示 outcome_unknown。修正仅在明确记录结束状态时使用该状态，增加 completed/cancelled/interrupted/unknown 回归，相关测试 30 passed。
13. 修正邀请制部署中的无效共享演示按钮，增加 VITE_PUBLIC_DEMO_ENABLED 构建开关，容器默认 false；前端 typecheck、93 项组件测试、18 项端到端测试及构建通过。额度、模型回执、子调用和写入回执回归另有 33 passed。

14. 最终显示修正 9d5e7c5 在 Render 显示 Deploy succeeded|Live。刷新公共页面后确认邀请制提示可见、共享演示按钮已隐藏；历史运行完整时间线显示 69 条记录，末项 journal.end 显示 completed。再次执行只读验收通过，12 条流式记录、两次成功模型调用、当日使用量 2 与账号隔离均保持；记录：logs/public_demo_final_read_20261007.json。页面证据：output/fitagent-public-live-20261007.png、output/fitagent-public-login-20261007.png。

公共演示已交付。完整背景定时执行器和在线向量嵌入没有上线；全量 Python CI 超时边界仍保留，不冒充全门禁通过。仅提交本次所属文件，未修改或提交另行出现的 agent_verifier.py 与对应测试改动。
