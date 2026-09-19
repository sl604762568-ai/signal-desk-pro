# 公网部署说明：Signal Desk Pro v4

这一版不再依赖本机 `127.0.0.1`。部署成功后平台会提供 HTTPS 公网域名，电脑、iPhone、Android 均可直接打开。

## 方案 A：Render（推荐）

### 1. 把本项目上传到 GitHub
保证以下文件位于仓库根目录：

- `Dockerfile`
- `render.yaml`
- `requirements.txt`
- `app.py`
- `static/`

### 2. Render 新建 Blueprint

1. 登录 Render。
2. `New` → `Blueprint`。
3. 连接刚才的 GitHub 仓库。
4. Render 会自动读取根目录 `render.yaml`。
5. 确认部署区域为 `Singapore`。
6. 点击部署。

部署完成后会得到类似：

`https://signal-desk-pro-xxxx.onrender.com`

直接在手机浏览器打开即可。

### 3. 验证

打开：

`https://你的域名/api/health`

看到 `"ok": true` 说明后台正常。

然后访问首页：

`https://你的域名/`

### 4. iPhone 添加到桌面

Safari 打开公网 HTTPS 地址 → 分享 → `添加到主屏幕`。

Android Chrome 也可以通过浏览器菜单安装 PWA/添加到主屏幕。

---

## 方案 B：Railway

1. 在 Railway 新建 Project。
2. 选择 `Deploy from GitHub repo`。
3. 选择本项目仓库。
4. Railway 会检测根目录 Dockerfile。
5. Variables 中加入 `.env.example` 内的变量。
6. 在 Networking 中创建 Public Domain。
7. 打开平台分配的 HTTPS 域名。

Dockerfile 已使用平台提供的 `$PORT`，无需自己写死端口。

---

## 公网环境变量

推荐值：

```env
CACHE_SECONDS=75
NEWSNOW_BASE_URL=https://newsnow.busiyi.world
NEWS_TIMEOUT=6
HOTSPOT_DESK_URL=https://hotspot-link-desk.sl604762568.chatgpt.site
DB_PATH=/tmp/signal_desk_sentiment.db
```

`NEWSNOW_BASE_URL` 推荐后续替换成你自己的 NewsNow 实例，避免公共实例限流。

---

## 为什么历史情绪可能重置

当前情绪历史用 SQLite 保存。Render/Railway 普通容器文件系统可能在重启/重新部署后重置，因此 `20日情绪历史` 不应视为永久数据库。

要永久保存，可以后续升级为：

- PostgreSQL（推荐）；或
- 平台 Persistent Volume / Persistent Disk。

实时行情、新闻、候选池不依赖历史库，数据库重置不会导致网站打不开。

---

## 常见部署问题

### 首页能打开，但行情显示演示数据

通常是 AKShare 上游接口在云服务器网络环境中暂时不可达、接口变更或非交易时段。页面会自动回退到演示模式，防止白屏。

### 新闻显示演示回退

NewsNow 公共实例可能限流。建议自建 NewsNow，之后只需要修改 `NEWSNOW_BASE_URL`。

### `/api/health` 正常，但首页数据慢

首次加载需要并发抓行情、新闻和热点链路，随后有 75 秒缓存。可以把 `CACHE_SECONDS` 提高到 120。

### 自定义域名

可以在 Render/Railway 的 Domain 设置中绑定自己的域名。绑定后仍应使用 HTTPS。

---

## 数据与交易提示

本项目的候选评分表示研究优先级，用于整理新闻、情绪和量价信息，不等于收益预测或交易指令。价格、成交、涨跌停状态最终以交易所及交易软件为准。
