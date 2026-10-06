# 本地开始：无需租服务器

从仓库根目录执行。准备 Python（后端运行环境）3.11 或 3.12、Node.js（前端构建环境）20.19 及以上兼容版本、PostgreSQL，以及**专用于本项目的空数据库与账号**。不要连接工作单位或已有重要数据的数据库。

```powershell
git clone https://github.com/threeing3/fitagents.git
cd fitagents
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r fast_api/requirements-dev.txt
cd web
npm ci
npm run build
cd ..
```

前端构建后由同一后端提供，不是另开的演示项目。

先离线体验：

```powershell
python -m scripts.run_local_byok --provider offline --ack-db-migrations
```

入口隐式询问数据库连接串，格式为 `postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@127.0.0.1:5432/YOUR_NEW_DATABASE`。密码特殊字符需要百分号编码。

**启动会自动迁移数据库并初始化内置知识。** 确认参数不是只读检查；已有库须先备份审计，不要指向旧业务库。

真实模型模式：

```powershell
python -m scripts.run_local_byok --provider qwen --model YOUR_MODEL_ID --ack-db-migrations
# 或 --provider deepseek --model YOUR_MODEL_ID
```

模型名称以自己的服务商账号权限为准。随后隐式输入自己的密钥；不会从旧配置回填作者密钥，也不提供额度。输入错误会导致请求失败。

打开 <http://127.0.0.1:8015/> 注册本地账号；端口占用可加 `--port 8017`。只监听本机。Ctrl+C 停止，不删除数据库。离线只能验证规则与业务状态，不能据此判断模型语言理解效果。

## 运行边界

- 不自动启动后台工作进程；长期责任不会只因页面启动就持续执行。不要在旧库随意运行全库后台扫描脚本。
- 默认关闭向量调用，使用已有词法检索降级路径，不伪造向量。
- 模型费用由用户承担。每日调用次数不是金额硬上限，请同时设置服务商额度。
- 单部署使用一套模型配置，不支持同一公网服务的账号分别配置密钥。
- 登录签名密钥在启动时临时生成，重启后需要重新登录，数据仍保留。

页面缺失：先构建 `web/dist/index.html`。数据库失败：检查服务、账号及空库地址。迁移失败：保留错误日志，不清库重试。模型失败：检查服务商权限、模型及额度，反馈问题时不要粘贴密钥或完整连接串。

本机管理员或调试器仍可能读取进程环境；不要在不可信电脑输入密钥。
