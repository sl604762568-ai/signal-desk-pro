# v6.4.1 前端热修复

修复 v6.4 `static/index.html` 中 `renderAll()` 少一个结束大括号的问题。该错误导致 `loadDashboard()` 被嵌套在 `renderAll()` 中，首页初始化时不会真正执行数据加载，因此后端 `/api/dashboard` 正常返回 JSON，但首页仍一直显示“加载中”。

部署后 `/api/health` 应显示 `6.4.1-frontend-hotfix`。

若手机/电脑仍显示旧页面，执行一次强制刷新；Service Worker 缓存名已升级为 `signal-desk-v6-4-1`。
