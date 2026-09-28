# v6.13 部署

1. 解压 ZIP。
2. 使用 GitHub Desktop 将全部文件覆盖到现有 `signal-desk-pro` 本地仓库。
3. Commit to main → Push origin。
4. 等 Render 自动部署。
5. 打开 `/api/health`，确认 `version` 为 `6.13-review-role-intraday-risk`。
6. 建议手机端首次更新后关闭旧页面重新打开；Service Worker 缓存号已升级到 v6.13。

## 上线后重点检查

- `/api/auction25/backfill?days=6` 只能用 POST；网页“补采今日/近5日”按钮已不要求管理员口令。
- `/api/dragon-tiger?force=true` 应能强制刷新龙虎榜；当日未披露时自动回退到最近有披露的交易日。
- `/api/sector-review` 若 Eastmoney clist 风控，会优先显示真实历史板块快照或真实涨停题材聚合，不填造资金数字。
- `/api/intraday-1430` 为14:30尾盘研究池；Render Free休眠时不能保证无人值守自动运行。
- `/api/global-risk` 返回 VIX / VXN / SP500 / NASDAQ / DOW 与A股情绪对照。
