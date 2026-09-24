# v6.12 覆盖升级（推荐 GitHub Desktop）

1. 先把当前 `signal-desk-pro` 仓库备份一份，以免原先的线上版本或本地交易历史丢失。
2. 下载本版本完整 ZIP，解压后确认根目录有 `app.py`、`terminal_explorer.py`、`sector_engine.py`、`static/index.html`。
3. 打开 GitHub Desktop → 当前仓库 `signal-desk-pro` → `Repository` → `Show in Explorer`，将压缩包中的**内容**复制到仓库根目录。覆盖同名文件，**不要只上传 ZIP 或只复制外层文件夹**。
4. 在 Desktop 中确认变更清单包含 `app.py`、`terminal_explorer.py`、`sector_engine.py`、`public_sources.py`、`static/index.html`、`static/sw.js`，再输入 `v6.12 sector + factor terminal` → `Commit to main` → `Push origin`。
5. 到 Render → 当前服务 → Events / Deploys，确认部署最新提交为 Live。
6. 测试 `/api/health` 的 `version` 为 `6.12-sector-factor-terminal`；`/api/boards/explorer/groups` 应返回真实热点分组名称。首页进入 `板块行情` 触发独立板块抓取；盘中到 `单项/组合选股` 选择单个条件测试。
7. 首次分钟信号没有采样窗口时 `--` 是正常的：开启每60秒刷新并保持页面活跃至少 2 分钟后，1分钟信号才有可能出现；10分钟振幅需采集约10分钟。Render 免费服务休眠或客户端离线可能中断记录。

如果 Render 加载失败，优先提供 `/api/health` 返回的版本号、Render 最近一条 Deployment 和 Logs 最后30行，不要把数据库密码或其他凭证发来。
