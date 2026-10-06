# 执行轨迹验收清单

本清单属于手工设计的合成故障验收，不是用户行为分布上的独立盲测，也不进入训练集。检查实际日志与业务状态，不按回复长度或流畅度评分。

| 场景 | 注入方式 | 必须满足 | 当前证据 |
| --- | --- | --- | --- |
| 未提交时硬退出 | 测试子进程收到 READY 后精确强制结束 | 日志前缀保留；隔离库零业务记录；轨迹未确认 | test_durable_stream_crash.py：before_commit |
| 提交后回复前硬退出 | 同上，在提交后发出同步事件 | 隔离库一条业务记录；无终止记录；不得推断允许重跑 | 同文件：after_commit |
| 无终止记录 | 保存前缀后直接关闭文件 | 不确认进程死亡，不自动重试 | test_durable_stream_journal.py |
| 用户断开流 | 关闭真实异步生成器 | 中断日志保留；未提交业务状态仍未提交 | 同文件：cancelled_stream |
| 收到完成后断开 | 接收完成事件再关闭流 | 不将已完成日志覆盖为中断 | 同文件：close_after_done |
| 磁盘同步失败 | 第二次同步抛出错误 | 事件不能发给前端；下一工具不执行 | test_durable_stream_crash.py：disk_sync_failure |
| 无效日志头 | 空文件、null、数组、损坏对象 | 不作为有效归属与证据 | 同文件：invalid_header |
| 损坏日志尾 | 完整终止记录后追加非对象行 | 明确缺损，整体未确认，不改写源文件 | 同文件：damaged_suffix |
| 账号或会话不符 | 外账号查询与错误归属 | 拒绝读取和错误子任务关联 | test_execution_trace.py / test_durable_stream_journal.py |
| 重复事件 | 同一事件存在于多个保存来源 | 稳定编号去重，不伪造新执行 | test_execution_trace.py |
| 历史刷新 | 前端加载保存回复再展开 | 保留运行编号；只读查询，不能触发工具写入 | web/e2e/recorded-trace.spec.ts |
| 缺损可见性 | 接口返回缺损和存活未核对字段 | 面板解释限制；无重跑按钮 | web/src/RecordedTracePanel.test.tsx |
| 训练写入提交核对 | 独立连接读取原消息、幂等完成凭据和训练记录 | 账号、会话、目标编号和提交时间一致才确认 | test_write_receipts.py |
| 未提交的完成标记 | 调用连接仅刷新、不提交 | 独立连接不能确认；不提交或回滚调用事务 | 同文件：uncommitted_receipt |
| 核对连接故障 | 独立连接抛出数据库异常 | 已提交记录保留；降级为未确认；不建议重复写入 | 同文件：verification_connection_failure |
| 过期成功凭据 | 工具保存成功但当前业务归属不符 | 追踪接口重新核对；工具状态与提交确认分开 | 同文件：trace_rechecks_receipt |
| 中断后已有提交 | 请求保留进行中状态且日志没有终止记录，业务凭据已提交 | 整体仍未确认；单独显示已提交训练凭据；禁止重写 | 同文件：interrupted_trace_and_status |
| 原请求关联未提交 | 只刷新聊天请求到原消息的关联，不提交 | 不能用调用连接可见的关联证明本请求已写入 | 同文件：uncommitted_request_link |
| 实际业务断流与重试 | 执行真实业务代码、离线模型，写入后关闭流 | 原请求保留，重试拒绝新执行，独立确认仅一条训练记录 | test_workout_conversation_chain.py：interrupted_stream_keeps |
| 批准但未执行 | 实际生成草案并批准，尚未运行工作器 | 不确认计划提交，不将批准当执行 | test_plan_write_receipts.py |
| 获批减组已提交 | 实际运行原后台工作器并提交 | 审批、任务、执行、责任事件、当前草案内容均核对一致 | 同文件：actual_worker_is_confirmed |
| 计划执行断链 | 改变目标、账号、任务状态、事件关联或提交时间 | 不确认提交，不自动重试 | 同文件：mismatched_commit_chain |
| 计划调整尚未提交 | 执行原调整代码后仅刷新，未提交事务 | 独立连接仍看到批准未执行，不改变调用事务 | 同文件：flushed_but_uncommitted |
| 执行结果未知 | 已有审批处于结果未知 | 不声称未执行，保持未确认 | 同文件：unknown_execution |
| 分页期间追加 | 读取第一页后追加事件及终止记录 | 原日志位置稳定，不漏读、不重复；状态与分页分别报告 | test_stream_event_pages.py |
| 未完成尾行 | 写入完整文本但无换行、编码截断或不完整对象 | 保留完整前缀，不推进恢复位置；补完后在同一下一位置读取 | 同文件：partial_tail / completed_tail |
| 错误恢复位置 | 别的日志、负数、非数字、超过现存完整记录的位置 | 拒绝恢复，不猜位置或自动重跑 | 同文件：cursor_cannot_cross |
| 增量权限 | 外账号、错误会话日志标记、缺少明确日志标记 | 拒绝读取，不靠相同时间或文本关联 | 同文件：endpoint / wrong_session_marker |
| 增量前端故障 | 下一页读取失败、组件卸载后收到迟到回复 | 保留已读记录和位置，忽略迟到回复，不触发业务执行 | web/src/StreamEventsPanel.test.tsx |
| 模型调用关联 | 真实回调协议、本地假模型 | 开始和结束编号一致，输入片段与公开返回可查看 | test_model_call_records.py |
| 客户端装配 | 替换网络客户端工厂，保留提供器实际装配代码 | 常规和意图工厂均安装记录器，作用域内产生实际事件 | 同文件：provider_installs_recorder |
| 模型开始写盘失败 | 调用前的持久记录抛出磁盘错误 | 回调错误阻止进入模型边界，不静默丢记录 | 同文件：start_persistence_failure |
| 模型并发隔离 | 两个任务交错使用同一假模型 | 输入只归属各自日志，作用域退出后不再记录 | 同文件：concurrent_calls / real_callback_protocol |
| 模型隐私 | 输入图片、推理块、异常中的敏感文本 | 省略图片地址与推理块，仅记录异常类型 | 同文件：media_and_hidden / error_record |
| 子任务模型归属 | 实际执行训练、饮食、恢复子循环 | 每次调用开始和结束带同一父子编号、激活和模型轮次；宿主接续不继承子来源 | test_subagent_call_trace.py |
| 子任务读取步骤 | 实际只读工具查询及接收前重核对 | 步骤编号配对；展示受限观察和读取阶段；错误只记类型 | 同文件：read_then_final / read_error |
| 五角色复盘交接 | 实际分析、三领域及候选规划循环 | 所有模型归属明确；交接前重读也归属原证据子任务 | 同文件：analysis_domains_and_planning |
| 子任务历史展示 | 实际保存日志经历史投影，再前端展开 | 父子与步骤编号、受限模型输入仍可见 | 同文件：history_projection / web/src/RecordedTracePanel.test.tsx |
| 调用中取消父任务 | 原子任务循环与真实回调协议中的本地阻塞模型 | 取消到达模型；只启动首领域；子任务失败状态保存；远端模型结果仍未知 | test_subagent_interrupt_trace.py：parent_cancel |
| 委派后关闭消费者 | 收到委派事件立即关闭生成器 | 保存待执行到终止状态；未发起模型调用 | 同文件：consumer_close |
| 子任务版本恢复 | 同一可继续子任务使用当前版本恢复，再提交旧版本 | 子任务编号不变，激活次数与步骤变化；旧版本不能新增调用 | 同文件：resume_keeps |
| 标签页位置恢复 | 保存位置后重新挂载及整页刷新 | 用账号与运行隔离的元数据续读，不缓存事件正文，不恢复执行 | web/src/StreamEventsPanel.test.tsx / web/e2e/trace-position-restore.spec.ts |
| 位置存储受限 | 浏览器存储抛出错误或损坏元数据 | 不影响当前只读查询；无错误账号恢复；可从头只读 | web/src/tracePosition.test.ts |
| 视觉客户端记录 | 真实客户端工厂装配，本地假模型执行原识别函数 | 公开结果可见，图片地址、编码和字节省略；离线明确跳过 | test_auxiliary_model_trace.py |
| 嵌入逻辑调用 | 本地确定性向量客户端及错误注入 | 起止编号一致，记录维度、耗时与降级，不记录正文或向量 | 同文件：embedding_records |
| 嵌入开始记录失败 | 追加记录抛出磁盘异常 | 不进入向量查询，不伪造降级后的成功 | 同文件：embedding_start_persistence_failure |
| 独立图片识别追踪 | 原图片接口、隔离文件数据库、本地假识别服务 | 返回运行编号；独立记录事务；正常与不可确认结果可按本人分页读取；不保存饮食 | test_standalone_nutrition_trace.py |
| 独立操作中断 | 异常、取消、终止记录存储失败 | 完整前缀保留；不替换原取消、不自动重试；缺少终止状态为未确认 | 同文件：error_and_cancel / terminal_storage_error |
| 后台任务记录 | 原队列、隔离文件数据库、假业务处理器 | 业务执行前独立保存诊断运行；完成、异常、取消均可按本人分页；取消不重排写任务 | test_background_task_trace.py |
| 直接周期扫描 | 原入口、本地假扫描与数据库故障注入 | 保存跨用户扫描汇总，不记录用户结果；取消、提交异常、提交后日志失败不伪造业务回滚 | test_periodic_scan_trace.py |
| 后台历史入口 | 原审批历史接口、关联校验、前端组件测试 | 只有同账号、同任务、同尝试且明确日志标记的运行可展开；只读查看，不触发批准或执行 | test_background_trace_reference.py / test_persistent_approvals.py / web/src/BackgroundTraceEntry.test.tsx |
| 审批后台真实进程强制退出 | 原队列与实际减组处理器、临时文件数据库、父进程终止确切测试子进程 | 执行前与提交前保留原计划；提交后可独立查证已修改；无终止日志仍未确认；重启不重复领取 | test_background_process_crash.py |
| 强制退出后网页接口读取 | 完整主应用路由、真实本机服务与原账号认证，隔离文件数据库 | 本人可读取未确认日志及独立提交结果；未登录及无效账号拒绝，其他有效账号不能读取；读取不重复写计划 | 同文件：before_commit / after_commit，trace_http_worker.py |

## 运行

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_durable_stream_crash.py tests/test_durable_stream_journal.py tests/test_execution_trace.py tests/test_chat_request_status_api.py -q
```

前端在 web 目录执行 `npm run test -- src/RecordedTracePanel.test.tsx src/ExecutionTimeline.test.tsx`。实际执行结果记录在 logs/experiments/20261005_trace_crash_acceptance.md。

## 尚未达成的验收范围

- 完整应用在 PostgreSQL 上的进程强杀和提交结果核对；本轮强杀使用真实独立进程、真实文件同步与隔离 SQLite，不伪装为生产系统故障。
  审批后减组的实际队列与处理器已覆盖执行前、提交前、提交后但追踪结束前、追踪结束后四个强制退出点，并重新读取审批、计划、任务和独立提交凭据。尚不是 HTTP 应用进程或 PostgreSQL 部署的完整验收。
  退出后已增加完整主应用网页接口重启读取，使用真实认证与原路由；测试禁用生命周期迁移与知识初始化。该验证仍不能替代网页应用进程自身中断、完整启动迁移或 PostgreSQL 部署测试。
- 在工具执行前、执行中、提交后及确认后分别注入故障，并把每次工具调用与业务提交凭据逐一关联。
  训练写入已完成工具记录与提交凭据关联，中断请求也可根据已提交原消息关联单独核对训练凭据；获批指定日期减组链路增加审批、任务、执行事件及当前计划的独立核对。其他计划写入与写工具尚未覆盖。
- 已有聊天日志作用域内，常规、意图及视觉客户端和嵌入逻辑调用具备受限记录；独立图片接口及后台队列执行入口现已建立日志作用域，直接周期扫描入口另有运维汇总日志，不进入用户追踪。独立图片接口拒绝共享连接内存数据库，验收使用文件数据库；前端暂无饮食图片上传页，未宣称完整上传交互已交付。领域与复盘协作的调用、实际读取及交接重核对已有父子归属；子任务本地取消和版本恢复已验收，但进程硬退出后的完整跨请求恢复尚未覆盖。输入截断或省略内容不能精确复现。单个追加日志的位置分页及当前标签页刷新恢复已实现，跨全部来源的统一顺序、跨设备位置同步尚未覆盖。
- 长期用户旅程的真实模型验收，不能用上述确定性故障测试替代。

后台队列执行入口已补日志作用域，领取提交后、业务执行前保存运行与任务关联；正常终止在原处理器及队列提交之后记录。本地取消不将业务任务重新排队；业务任务可能仍为 running（运行中，需另行核对），不能用本地中断证明业务未提交。计划写入的原审批、回滚与只执行一次测试仍需通过。后台周期扫描的直接调用入口现有仅运维可读的汇总日志，不进入用户追踪接口，也不记录跨用户业务结果；其日志不能独立证明数据库提交。完整 PostgreSQL 进程强杀仍未验收。

目标保持为持续完善执行轨迹与验收集，以上清单通过不表示整个目标完成。
