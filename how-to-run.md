这个项目主要有 8 个启动入口。第一次运行前，先在项目根目录激活虚拟环境并完成配置：

```powershell
cd D:\agent\hermes-agent
.\.venv\Scripts\Activate.ps1
hermes setup
```

如果 PowerShell 找不到 `hermes`，可以先以开发模式安装：

```powershell
uv pip install -e ".[dev,web,messaging,acp]"
```

### 1. 经典命令行聊天

```powershell
hermes
```

这是最基础的入口，对应 [cli.py](D:/agent/hermes-agent/cli.py:15759)。

### 2. 现代终端 TUI

```powershell
hermes --tui
```

适合日常使用，拥有更现代的终端界面。

开发 TUI：

```powershell
cd ui-tui
npm install
npm run dev
```

脚本定义在 [ui-tui/package.json](D:/agent/hermes-agent/ui-tui/package.json)。

### 3. Telegram、Discord、Slack 等消息网关

先配置平台：

```powershell
hermes gateway setup
```

前台启动：

```powershell
hermes gateway
# 或
hermes gateway run
```

后台服务管理：

```powershell
hermes gateway install
hermes gateway start
hermes gateway status
hermes gateway restart
hermes gateway stop
```

Windows/WSL 环境如果服务模式有问题，优先使用：

```powershell
hermes gateway run
```

核心实现位于 [gateway/run.py](D:/agent/hermes-agent/gateway/run.py)。

### 4. Web 管理面板

```powershell
hermes dashboard
```

指定端口、不自动打开浏览器：

```powershell
hermes dashboard --port 8080 --no-open
```

查询或关闭面板：

```powershell
hermes dashboard --status
hermes dashboard --stop
```

Dashboard 包含配置、密钥、会话和聊天管理。原生 Windows 上嵌入式 TUI 聊天的 PTY 支持可能受限，WSL2 更合适。

只开发 Web 前端：

```powershell
cd web
npm install
npm run dev
```

脚本位于 [web/package.json](D:/agent/hermes-agent/web/package.json)。

### 5. Headless 后端服务

```powershell
hermes serve
```

它启动和 Dashboard 相同的 API、WebSocket、JSON-RPC 后端，但不提供浏览器页面，主要供桌面客户端或远程客户端连接：

```powershell
hermes serve --host 127.0.0.1 --port 8080
```

### 6. Electron 桌面应用

桌面端要求 Node.js 20.19+ 或 22.12+。依赖从仓库根工作区安装：

```powershell
cd D:\agent\hermes-agent
npm install
cd apps\desktop
npm run dev
```

生产方式启动：

```powershell
npm start
```

构建 Windows 安装包：

```powershell
npm run dist:win
```

桌面端会自行启动 `hermes serve` 后端。脚本见 [apps/desktop/package.json](D:/agent/hermes-agent/apps/desktop/package.json)。

### 7. 定时任务 Cron

Cron 任务由运行中的 Gateway 定期触发，不需要单独启动一个长期 Cron 进程：

```powershell
hermes gateway run
```

另开终端管理任务：

```powershell
hermes cron create
hermes cron list
hermes cron run <job_id>
hermes cron pause <job_id>
hermes cron resume <job_id>
hermes cron remove <job_id>
hermes cron status
```

手动执行一次调度检查：

```powershell
hermes cron tick
```

实现位于 [cron/scheduler.py](D:/agent/hermes-agent/cron/scheduler.py) 和 [hermes_cli/cron.py](D:/agent/hermes-agent/hermes_cli/cron.py)。

### 8. 编辑器 ACP 服务

用于 VS Code、Zed、JetBrains 等支持 ACP 的编辑器：

```powershell
hermes acp
```

等价入口：

```powershell
hermes-acp
python -m acp_adapter
```

代码位于 [acp_adapter](D:/agent/hermes-agent/acp_adapter)。

另外，项目文档网站可以这样启动：

```powershell
cd website
npm install
npm start
```

完整命令参考在 [cli-commands.md](D:/agent/hermes-agent/website/docs/reference/cli-commands.md:27)。建议学习顺序是：先运行 `hermes`，再运行 `hermes --tui`，之后研究 `gateway`，最后看 Dashboard 和 Desktop。