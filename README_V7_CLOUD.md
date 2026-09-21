# v7.0 云端常驻虚拟盘

本版让虚拟盘在手机、电脑都关闭时仍在 Render 云端运行。

## 自动流程（北京时间）
- 09:25 起：云端主动刷新实时行情。
- 09:35–10:45：按前一交易日“次日5股”与实时条件允许虚拟开仓。
- 交易时段：约每 60 秒检查持仓，执行止损、止盈、移动止盈和关键位破位。
- A股按 100 股一手，且模拟 T+1：当天买入的股票当天不可卖出。
- 15:05 后：自动生成并持久化当天“次日5股”，无需打开网页。

## Render 配置
`render.yaml` 已切换为：
- Web Service：`0.5c-512mb`（付费常驻，不休眠）
- Persistent Disk：1GB，挂载 `/var/data`
- 数据库：`/var/data/signal_desk.db`

## 验证
- `/api/health`：版本 `7.0-cloud-paper`
- `/api/paper/daemon`：`cloud_daemon: true`
- 盘中 `market_refresh.state` 应在 `running` / `ok` 之间变化。
