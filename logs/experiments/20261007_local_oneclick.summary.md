# 本机启动入口验证 — 2026-10-07

## 动机与修改

将现有隔离数据库启动、表结构核对和应用启动合并成一个入口，避免手工配置密钥及误执行迁移、后台遗留任务。
新增根目录 Start-FitAgent.cmd、scripts/start-fitagent.ps1、scripts/run_local_preview.py、tests/test_local_preview.py 及启动说明；未修改本地环境变量文件、数据库迁移或依赖。

## 执行与结果

1. `.venv\Scripts\python.exe -m pytest tests/test_local_preview.py -q --no-cov`：1 passed。验证离线覆盖配置、固定隔离数据库和无真实密钥配置。
2. 初次 Ruff 检查发现导入排序与格式问题；通过自动格式工具修正。随后 `ruff check scripts/run_local_preview.py tests/test_local_preview.py` 与 `ruff format --check scripts/run_local_preview.py tests/test_local_preview.py` 均通过。
3. `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-fitagent.ps1 -Offline -CheckOnly -NoBrowser`：启动既有数据库，结构核对通过。数据库日志显示上次非正常退出，PostgreSQL 自动恢复后成功接受连接；没有删除、重置或手动修复数据。
4. 隐藏启动 PowerShell，执行 `scripts/start-fitagent.ps1 -NoBrowser`，输出保存在 `logs/oneclick_app_20261007.stdout.log` 和 `.stderr.log`。应用首次导入较慢，早期网页连接拒绝；待日志显示监听后复验通过。
5. GET `/health/ready`：status=ready；database=ok；migration=017_domain_jsonb_alignment；model=configured；embedding=offline。GET `/`：200。
6. 再次执行 `-CheckOnly -NoBrowser`：识别已运行应用，未重复启动。

## 边界与下一步

未调用真实模型验证输出质量；model=configured 仅证明已配置。未执行全仓库测试或推送。后台任务禁用。公共部署正在处理平台账号验证；独立公共数据库、完整追踪持久性和公网安全仍需验收，不等同上线完成。
