# Signal Desk Pro v6.11 — 完整项目重新打包与检查

这次重新打包以先前交付的 v6.11 项目为基础，**原有 29 个文件逐一校验 SHA-256，代码内容未被替换成旧版**。同时从上一版 v6.10 官方交付项目恢复了 13 个漏装的原始文件：`.gitignore`、`.dockerignore`、`.env.example` 与早期版本说明。压缩包根目录直接放 `app.py`、`render.yaml`、`requirements.txt`、`static/`，没有额外一层文件夹，也没有开发缓存或数据库文件。

## 请先知道

- 此压缩包**需要先解压**，不要将 ZIP 自身提交到 GitHub。
- 如果 GitHub 网页单个 `app.py` 上传后的 `Commit changes` 仍返回 **HTTP 400**，说明 GitHub 提交环节还有问题，**重新打包并不能保证解决这个 HTTP 错误**。建议使用 GitHub Desktop 克隆已有仓库，再复制当前压缩包解压后的全部文件并提交、推送。
- 不要删除旧仓库，不要上传本地 `*.db`、`.env`、密钥，也不要顺手重置 Render。
- Render Free 的 `/tmp` SQLite 会丢失历史快照。反复部署前先备份仍可访问的自选股、回测等记录；此压缩包不附带任何用户历史数据。
- 数据源在线率、真实 09:25 竞价归档或全天无人值守不属于本地检查通过的范围，部署后需要实际验证。

## 包含的功能与限制

沿用 v6.11 的收盘复盘固定结果、龙虎榜汇总、板块轮动、严格技术条件、自选股和独立集合竞价页面；保留单股诊断和虚拟盘。未经核实的龙虎榜席位性质不会杜撰。竞价历史必须真实逐日采集，不能由任意盘中行情补算。

## 已完成的本地检查

1. 先前 v6.11 原有文件 SHA-256 逐一校验，没有漏掉源代码。
2. `python -m compileall -q .` 检查全部 Python 模块。
3. `node --check` 检查网页内联 JavaScript。
4. `python test_v611_offline.py` 对固定选股、历史技术条件、竞价时间、防虚构数据、自选和 API 做不联网测试。
5. FastAPI 首页与健康接口等在本地进行烟雾测试。
6. ZIP 完整性 `ZipFile.testzip()` 检查，并校验必备根目录文件、静态资源与部署文件。

## GitHub Desktop 更新（避开网页上传 HTTP 400）

1. Desktop 登录原 GitHub 账户，`File → Clone repository`，选择 `sl604762568-ai/signal-desk-pro`。
2. 先关闭网页上的上传弹窗。打开本 ZIP 解压目录，将**里面的文件和 static 文件夹**复制到 Desktop 克隆出来的仓库文件夹；Windows 提示同名文件时选覆盖。
3. Desktop 左侧核对改动清单，在 Summary 写 `Repackage and verify v6.11`；点 `Commit to main` 再点 `Push origin`。
4. 等 Render 重新部署，在 `/api/health` 核对 `6.11-auction-watchlist`。如果失败，查看 Render Deploy Logs 而不是反复上传。
