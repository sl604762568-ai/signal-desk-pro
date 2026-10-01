# v6.14 更新到现有 Render 网站

1. 解压本 ZIP。
2. 打开 GitHub 仓库 `sl604762568-ai/signal-desk-pro`。
3. `Add file` → `Upload files`，把解压后目录里的全部文件上传覆盖同名文件。
4. `Commit changes`。
5. Render 的 `signal-desk-pro` 会随 GitHub 提交自动部署；若没有自动开始，在 Render → Deploys 选择最新提交部署。
6. 部署完成后先访问 `/api/health`，再打开 `/market`。
7. 电脑端将看到左侧多页面导航；手机端使用底部导航。

本版没有更改你的 CONTROL_TOKEN，也没有新增必须配置的环境变量。
