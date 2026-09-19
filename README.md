# Signal Desk Pro v4 · Public Web

公网部署版：把「热点链路」与 A 股情绪周期、财经新闻雷达、短线量价候选池整合到一个手机/电脑自适应网页。

## 直接部署

- Render：根目录已经提供 `render.yaml` + `Dockerfile`，推荐区域 `Singapore`。
- Railway：根目录已经提供 `railway.json` + `Dockerfile`。
- 两个平台部署完成后都会得到 HTTPS 公网地址，不再使用 `127.0.0.1`。

详细步骤见：`DEPLOY_PUBLIC.md`。

## 核心接口

- `/`：工作台首页
- `/api/dashboard`：行情、新闻、热点链路、候选池聚合
- `/api/stock/{code}`：近 70 个交易日 K 线
- `/api/health`：公网部署健康检查

## 环境变量

见 `.env.example`。

## 数据回退

真实行情 / 新闻 / 原热点站任一上游失败时，系统会保留页面并回退演示结构，不会因为单一数据源错误导致整站白屏。

## 提示

候选评分表示研究优先级，不预测收益，不构成交易指令。实时价格与成交信息以交易所和交易软件为准。
