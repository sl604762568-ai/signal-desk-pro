# v6.15 白色量化研究工作台部署

1. 解压 ZIP，进入包含 `app.py`、`Dockerfile`、`render.yaml`、`static/` 的目录。
2. 将目录内全部文件覆盖上传到 GitHub 仓库 `sl604762568-ai/signal-desk-pro` 根目录并 Commit。
3. Render 会自动部署；若未触发，进入 `signal-desk-pro` → Manual Deploy → Deploy latest commit。
4. 部署成功后打开 `/api/health`，确认：`"version":"6.15-white-quant-workbench"`。
5. 浏览器执行一次强制刷新（Windows: Ctrl+F5；手机可关闭页面后重新打开），新版 Service Worker 会替换旧缓存。

## 本版重点
- 全站白色主题、PC/手机单屏密度优化。
- 单股新增 BOLL/KDJ/ATR/ADX/CCI/WR/MFI/OBV/BIAS 技术矩阵。
- 单股联动大盘情绪/市场广度、真实板块热度/题材关系与缠论近似结构。
- 个股K线增加 BOLL 上/中/下轨。
- 只读 GET 请求短时内存缓存 + 并发去重，减少重复拉取。
- 复盘选股改成后台线程扫描，前端轮询结果，避免长请求卡住页面。
