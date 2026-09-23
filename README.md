# 曜石决策台

A股短线情绪、题材、集合竞价、策略预警、自选监控与缠论结构分析网页。

## 本次修复

- “今日细分题材合集”改为独立加载，不再等待大盘与涨停池接口。
- 题材行情按批次读取，并每 2 分钟自动刷新。
- 接口异常会显示明确原因，可点击“刷新题材行情”手动重试。

## 上传到 GitHub

1. 登录 GitHub，点击右上角 `+`，选择 **New repository**。
2. 仓库名称建议填写：`a-share-radar-2026`。
3. 创建仓库后，选择 **uploading an existing file**。
4. 解压本压缩包，将 `index.html`、`.nojekyll` 和 `README.md` 上传到仓库根目录。
5. 点击 **Commit changes** 保存。

## 开启 GitHub Pages

1. 进入仓库的 **Settings → Pages**。
2. 在 **Build and deployment** 中选择 **Deploy from a branch**。
3. Branch 选择 `main`，目录选择 `/ (root)`，点击 **Save**。
4. 等待约 1–3 分钟，GitHub 会生成网页地址：

   `https://你的GitHub用户名.github.io/a-share-radar-2026/`

## 注意

- `index.html` 必须放在仓库根目录，不能只上传外层文件夹。
- 网页行情来自公开行情接口；接口异常或缺少真实数据时，页面会显示缺失状态，不会用模拟数据冒充。
- 9:25竞价历史快照保存在浏览器本机；更换电脑、清除浏览器数据或使用其他域名后不会自动同步。
