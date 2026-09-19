# v6.8.2 Live Refresh Fix

修复 v6.8.1 后台真实行情无法写入缓存的问题。

根因：`DirectPublicProvider.fetch` 未声明 `fast` 参数，但后台刷新调用 `provider.fetch(fast=True)`，导致真实行情子进程直接抛出 TypeError。

本版修改：
- `DirectPublicProvider.fetch(self, fast: bool=False)`
- 版本号更新为 `6.8.2-live-refresh-fix`
- Service Worker 缓存版本更新

部署后检查：
1. `/api/health` 应显示 `6.8.2-live-refresh-fix`
2. 刷新首页后约数秒至几十秒，`refresh.has_live_cache` 应变为 `true`
3. `/api/dashboard` 的 `is_live` 应为 `true`，`served_from` 应为 `live-cache`
