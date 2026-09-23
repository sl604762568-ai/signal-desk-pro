# v6.11 一次升级到你现有的网站

1. 把 ZIP **解压**，进入包含 `app.py`、`Dockerfile`、`render.yaml` 和 `static/` 的文件夹。
2. 打开原有 GitHub `sl604762568-ai/signal-desk-pro`，选择 `Add file` → `Upload files`，把**文件夹里的全部文件**拖进去，同名文件直接更新，然后 `Commit changes`。
3. Render → `signal-desk-pro` → Environment：如需添加自选、手动竞价捕获或完整历史股性扫描，设置一个**自己生成的、16位以上**的 `CONTROL_TOKEN`。不要将其发给任何人；只读功能无须设置。
4. Render → Deploys，等待最新提交变成 `Live`；访问 `https://signal-desk-pro.onrender.com/api/health`，确认版本 `6.11-auction-watchlist`。
5. 强制刷新手机/电脑网站。上方菜单应按“收盘复盘、龙虎榜、09:25竞价、明日五股、自选股、单股分析……自动回测”排列。

**注意：保持 Render 免费版意味着既不能保证9:25自动采集成功，也无法保证 SQLite 中的冻结选股、自选、历史竞价和虚拟盘在实例重启/新部署后不丢失。** 如要求全天候无人值守或长期历史研究，后续必须使用常驻实例与外部持久数据库。本版不自动连接 Supabase，也不收取费用。
