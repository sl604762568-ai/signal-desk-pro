from __future__ import annotations

import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

import requests

NEWSNOW_BASE = os.getenv("NEWSNOW_BASE_URL", "https://newsnow.busiyi.world").rstrip("/")
NEWS_TIMEOUT = float(os.getenv("NEWS_TIMEOUT", "6"))

SOURCES = {
    "cls-hot": "财联社热门",
    "cls-telegraph": "财联社电报",
    "xueqiu-hotstock": "雪球热门股票",
    "wallstreetcn-quick": "华尔街见闻快讯",
    "jin10": "金十数据",
    "gelonghui": "格隆汇",
    "mktnews": "MKTNews",
    "fastbull": "FastBull",
}

SOURCE_WEIGHT = {
    "财联社电报": 1.18, "财联社热门": 1.12, "华尔街见闻快讯": 1.08,
    "金十数据": 1.04, "MKTNews": 1.04, "FastBull": 1.02,
    "格隆汇": 1.0, "雪球热门股票": 0.92,
}

POSITIVE = ["中标", "订单", "签约", "增持", "回购", "上调", "突破", "创新高", "涨价", "扩产", "量产", "获批", "超预期", "扭亏", "增长", "落地", "扶持", "提振", "减税", "降息", "并购", "重组"]
NEGATIVE = ["减持", "立案", "处罚", "问询", "下调", "亏损", "暴跌", "终止", "取消", "违约", "召回", "停产", "退市", "风险提示", "监管", "调查", "爆雷", "低于预期"]
TOPIC_RULES = {
    "机器人": ["机器人", "人形", "减速器", "丝杠", "执行器", "灵巧手"],
    "AI算力": ["AI", "人工智能", "算力", "GPU", "服务器", "数据中心", "CPO", "光模块"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "存储", "封装"],
    "新能源车": ["新能源汽车", "新能源车", "汽车", "动力电池", "智驾", "自动驾驶"],
    "锂电": ["锂电", "电池", "正极", "负极", "电解液", "固态电池"],
    "光伏储能": ["光伏", "储能", "逆变器", "太阳能"],
    "电力电网": ["电力", "电网", "变压器", "特高压", "用电", "核电", "水电"],
    "有色资源": ["黄金", "铜", "铝", "稀土", "锂矿", "有色", "小金属"],
    "军工航天": ["军工", "卫星", "航天", "低空", "无人机", "商业航天"],
    "医药": ["医药", "创新药", "医疗", "CXO", "疫苗", "生物"],
    "消费": ["消费", "白酒", "食品", "零售", "旅游", "家电"],
    "金融": ["证券", "券商", "银行", "保险", "金融", "资本市场"],
    "地产基建": ["地产", "房地产", "基建", "水泥", "建材", "城市更新"],
    "农业": ["农业", "种业", "粮食", "猪价", "糖", "大豆", "玉米"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Cache-Control": "no-cache",
}


def _parse_time(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v > 1e12:
            v /= 1000
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
        except Exception:
            return None
    s = str(raw).strip()
    if not s:
        return None
    return s


def _normalize_item(item: Dict[str, Any], source_id: str, source_name: str, rank: int) -> Dict[str, Any]:
    extra = item.get("extra") or {}
    title = str(item.get("title") or item.get("name") or "").strip()
    url = str(item.get("url") or item.get("mobileUrl") or "").strip()
    pub = _parse_time(item.get("pubDate") or item.get("date") or extra.get("date"))
    important = bool(item.get("important") or extra.get("important") or item.get("hot"))
    return {
        "source_id": source_id,
        "source": source_name,
        "title": title,
        "url": url,
        "pub_date": pub,
        "rank": rank,
        "important": important,
    }


def fetch_source(source_id: str, source_name: str, limit: int = 30) -> Tuple[List[Dict[str, Any]], str | None]:
    url = f"{NEWSNOW_BASE}/api/s"
    try:
        r = requests.get(url, params={"id": source_id, "latest": ""}, headers=HEADERS, timeout=NEWS_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        raw_items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(raw_items, list):
            raw_items = []
        items = [_normalize_item(x, source_id, source_name, i + 1) for i, x in enumerate(raw_items[:limit]) if isinstance(x, dict)]
        return [x for x in items if x["title"]], None
    except Exception as exc:
        return [], f"{source_name}: {type(exc).__name__}"


def _sentiment(title: str) -> Tuple[str, float]:
    pos = sum(1 for w in POSITIVE if w.lower() in title.lower())
    neg = sum(1 for w in NEGATIVE if w.lower() in title.lower())
    if pos > neg:
        return "positive", min(1.0, 0.35 + 0.18 * pos)
    if neg > pos:
        return "negative", min(1.0, 0.35 + 0.18 * neg)
    return "neutral", 0.2


def _topics(title: str) -> List[str]:
    result = []
    low = title.lower()
    for topic, words in TOPIC_RULES.items():
        if any(w.lower() in low for w in words):
            result.append(topic)
    return result


def build_news_radar(max_per_source: int = 24) -> Dict[str, Any]:
    started = time.time()
    all_items: List[Dict[str, Any]] = []
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        jobs = {ex.submit(fetch_source, sid, name, max_per_source): sid for sid, name in SOURCES.items()}
        for fut in as_completed(jobs):
            items, err = fut.result()
            all_items.extend(items)
            if err:
                errors.append(err)

    # 标题去重：去空白/标点后前 40 字近似 key。
    dedup: Dict[str, Dict[str, Any]] = {}
    for item in all_items:
        key = re.sub(r"[\W_]+", "", item["title"].lower())[:48]
        if not key:
            continue
        if key not in dedup or item["rank"] < dedup[key]["rank"]:
            dedup[key] = item
    items = list(dedup.values())

    topic_counter: Counter[str] = Counter()
    topic_sent: Dict[str, float] = Counter()
    source_counter: Counter[str] = Counter()
    for it in items:
        direction, strength = _sentiment(it["title"])
        its_topics = _topics(it["title"])
        it["sentiment"] = direction
        it["topics"] = its_topics
        base = max(0.25, 1.15 - (it["rank"] - 1) * 0.025) * SOURCE_WEIGHT.get(it["source"], 1.0)
        if it["important"]:
            base *= 1.15
        it["heat"] = round(min(100, base * 70), 1)
        source_counter[it["source"]] += 1
        for t in its_topics:
            topic_counter[t] += 1
            topic_sent[t] += strength if direction == "positive" else (-strength if direction == "negative" else 0)

    clusters = []
    for topic, count in topic_counter.most_common(12):
        avg_sent = topic_sent[topic] / max(1, count)
        direction = "偏正" if avg_sent > 0.12 else ("偏负" if avg_sent < -0.12 else "中性")
        cluster_items = [x for x in items if topic in x["topics"]]
        heat = min(100, round(sum(x["heat"] for x in cluster_items[:10]) / max(1, min(10, len(cluster_items))) + min(count, 8) * 3, 1))
        clusters.append({"topic": topic, "count": count, "heat": heat, "direction": direction, "sentiment_raw": round(avg_sent, 3), "headlines": [x["title"] for x in cluster_items[:3]]})

    items.sort(key=lambda x: (x["important"], x["heat"], -x["rank"]), reverse=True)
    return {
        "source": f"NewsNow ({NEWSNOW_BASE})",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "items": items[:100],
        "clusters": clusters,
        "source_counts": dict(source_counter),
        "errors": errors,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def demo_news_radar() -> Dict[str, Any]:
    demo = [
        ("财联社电报", "机器人产业标准与量产进度持续推进，核心零部件关注度升温", "机器人", "positive"),
        ("华尔街见闻快讯", "AI 数据中心资本开支预期维持高位，光模块与电力链条受关注", "AI算力", "positive"),
        ("金十数据", "海外铜价波动加剧，资源品交易关注供需与美元变化", "有色资源", "neutral"),
        ("格隆汇", "新能源车产业链进入新车型与订单验证窗口", "新能源车", "positive"),
    ]
    items = []
    for i, (src, title, topic, sent) in enumerate(demo, 1):
        items.append({"source_id":"demo","source":src,"title":title,"url":"","pub_date":None,"rank":i,"important":i<=2,"sentiment":sent,"topics":[topic],"heat":88-i*5})
    return {"source":"演示新闻","updated_at":datetime.now().astimezone().isoformat(timespec="seconds"),"items":items,"clusters":[
        {"topic":"机器人","count":5,"heat":92,"direction":"偏正","sentiment_raw":0.4,"headlines":[demo[0][1]]},
        {"topic":"AI算力","count":4,"heat":86,"direction":"偏正","sentiment_raw":0.35,"headlines":[demo[1][1]]},
        {"topic":"有色资源","count":3,"heat":68,"direction":"中性","sentiment_raw":0.0,"headlines":[demo[2][1]]},
    ],"source_counts":{"演示":4},"errors":["当前为演示回退"],"elapsed_ms":0}
