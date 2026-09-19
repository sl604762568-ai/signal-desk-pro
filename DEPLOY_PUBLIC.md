# v6.4 覆盖部署（你现在已有 Render 服务）

你不需要重新创建 GitHub 仓库，也不需要重新创建 Render。

1. 解压 `signal_desk_pro_v6_4_direct.zip`。
2. 打开 GitHub：`sl604762568-ai / signal-desk-pro`。
3. 点击 `Add file` → `Upload files`。
4. 把解压后文件夹**里面的所有文件和文件夹**拖进去（不是上传 ZIP）。
5. 页面底部点击 `Commit changes`。
6. Render 会自动部署；等状态变成 `Live`。

## 第一个检查

打开：

`https://signal-desk-pro.onrender.com/api/health`

应看到：

`"version":"6.4-direct-public"`

## 第二个检查（最重要）

打开：

`https://signal-desk-pro.onrender.com/api/sources`

这个页面会直接告诉你三个源：

- `sina_list`：新浪行情列表
- `sina_count`：新浪 A 股数量
- `tencent_kline`：腾讯日 K

如果至少新浪列表和腾讯 K 线为 `ok:true`，网站就具备真实个股行情和历史结构分析能力。

## 第三个检查

打开 `/api/dashboard`。它不会再同步等待外部源；冷启动时先返回结构/缓存，然后后台刷新。网页会自动轮询。

如果首页仍显示旧界面，请按 `Ctrl+F5` 强制刷新一次。v6.4 已升级 Service Worker 并自动删除旧缓存。


## v6.8 部署后检查

- `/api/health` 应显示 `6.8-sector-rotation`。
- `/api/sector-review` 应返回 `sectors` 与 `rotation`。
- 点击任意板块后 `/api/sector/BKxxxx` 应返回真实成分股。
