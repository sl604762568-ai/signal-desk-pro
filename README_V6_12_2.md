# v6.12.2 依赖冲突修复

- 修复 Render pip `ResolutionImpossible`。
- 将 `uvicorn[standard]` 改为普通 `uvicorn`，避免引入 `watchfiles` 等生产环境不需要的可选依赖。
- 保留 v6.12.1 的竞价多源采集与历史补采逻辑。
- 健康检查版本：`6.12.2-dependency-fix`。
