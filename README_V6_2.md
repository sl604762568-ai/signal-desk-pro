# Signal Desk Pro v6.2 稳定数据版

本版针对 Render 公网部署后首页长期“数据加载中”修复：

- AKShare/requests 默认 6 秒网络超时，避免第三方行情请求无限挂起。
- 首页先快速显示演示骨架，再尝试真实行情；真实源异常时不再白屏十分钟。
- 浏览器等待真实 dashboard 最多 25 秒，超时后保留当前可用数据。
- `/api/health` 版本显示 `6.2-stable-data`，便于确认 Render 已部署最新版。

部署：解压后把全部文件上传到原 GitHub 仓库并 Commit，Render 会自动重新部署。
