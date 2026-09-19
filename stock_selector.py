from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import pandas as pd

TOPIC_TO_INDUSTRY = {
    "机器人": ["机器人", "自动化", "机械", "电机", "减速器", "专用设备"],
    "AI算力": ["通信", "光通信", "计算机", "软件", "电子", "服务器", "数据中心"],
    "半导体": ["半导体", "元件", "电子", "芯片"],
    "新能源车": ["汽车", "汽车零部件", "电池", "新能源"],
    "锂电": ["电池", "锂电", "化工", "有色"],
    "光伏储能": ["光伏", "电力设备", "新能源", "储能"],
    "电力电网": ["电力", "电网", "电气设备", "电力设备"],
    "有色资源": ["有色", "贵金属", "小金属", "矿业", "煤炭"],
    "军工航天": ["军工", "航空", "航天", "船舶", "国防"],
    "医药": ["医药", "医疗", "生物", "制药"],
    "消费": ["食品", "饮料", "家电", "零售", "旅游", "纺织", "消费"],
    "金融": ["证券", "银行", "保险", "金融"],
    "地产基建": ["地产", "建筑", "建材", "水泥", "基建"],
    "农业": ["农业", "种业", "养殖", "农牧", "食品"],
}


def clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, float(v)))


def n(v: Any, d: float = 0.0) -> float:
    try:
        if pd.isna(v): return d
        return float(v)
    except Exception:
        return d


def _topic_match(industry: str, name: str, news: Dict[str, Any], hotspot_codes: set[str]) -> Tuple[float, List[str]]:
    hits: List[str] = []
    score = 0.0
    for c in news.get("clusters", [])[:12]:
        topic = str(c.get("topic", ""))
        aliases = TOPIC_TO_INDUSTRY.get(topic, [])
        if any(a in industry for a in aliases) or topic in industry:
            heat = n(c.get("heat"))
            score = max(score, heat)
            hits.append(f"{topic}热度{heat:.0f}")
    # 股票名被新闻标题直接点名，权重更高。
    direct = [x for x in news.get("items", [])[:100] if name and name in str(x.get("title", ""))]
    if direct:
        score = max(score, min(100, 76 + len(direct) * 6))
        hits.append(f"新闻直接提及{len(direct)}次")
    if any(code in hotspot_codes for code in []):
        pass
    return clamp(score), hits[:3]


def _volume_price_score(s: Dict[str, Any]) -> Tuple[float, List[str], List[str]]:
    pct = n(s.get("pct")); vr = n(s.get("volume_ratio")); tr = n(s.get("turnover_rate")); amount = n(s.get("amount"));
    high = n(s.get("high")); low = n(s.get("low")); price = n(s.get("price")); op = n(s.get("open")); prev = n(s.get("prev_close"));
    signals: List[str] = []; risks: List[str] = []
    score = 0.0
    # 量比 1.2~3.5 更健康，过热不继续加分。
    if 1.2 <= vr <= 3.5:
        score += 24; signals.append(f"量比{vr:.2f}×")
    elif 0.9 <= vr < 1.2 or 3.5 < vr <= 5:
        score += 15
    elif vr > 5:
        score += 10; risks.append("量比过热")
    else:
        score += 5
    # 换手活跃但避免极端。
    if 3 <= tr <= 18:
        score += 22; signals.append(f"换手{tr:.1f}%")
    elif 1.5 <= tr < 3 or 18 < tr <= 28:
        score += 14
    elif tr > 28:
        score += 8; risks.append("换手过高")
    else:
        score += 5
    # 涨幅处于强势但非极端区间。
    if 2 <= pct <= 8.5:
        score += 20; signals.append(f"涨幅{pct:+.1f}%")
    elif 0 < pct < 2 or 8.5 < pct < 9.8:
        score += 13
    elif pct >= 9.8:
        score += 8; risks.append("接近/触及涨停")
    else:
        score += 3
    # 日内收在高位。
    pos = (price - low) / (high - low) if high > low else 0.5
    if pos >= 0.78:
        score += 18; signals.append("日内收于高位")
    elif pos >= 0.58:
        score += 11
    else:
        score += 4
    # 成交额提供流动性分。
    if amount >= 8e8: score += 16
    elif amount >= 3e8: score += 12
    elif amount >= 1e8: score += 7
    else: score += 2; risks.append("成交额偏小")
    if prev > 0 and op > 0:
        gap = (op / prev - 1) * 100
        if gap > 5: risks.append("高开幅度较大")
    return clamp(score), signals[:4], risks[:4]


def _history_features(ak: Any, code: str) -> Dict[str, Any]:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=55)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is None or len(df) < 8:
            return {}
        c = pd.to_numeric(df["收盘"], errors="coerce")
        v = pd.to_numeric(df["成交量"], errors="coerce")
        last = float(c.iloc[-1])
        ma5 = float(c.tail(5).mean()); ma10 = float(c.tail(10).mean()) if len(c)>=10 else ma5; ma20 = float(c.tail(20).mean()) if len(c)>=20 else ma10
        vol5 = float(v.tail(5).mean()) if len(v)>=5 else 0
        vol_ratio5 = float(v.iloc[-1] / vol5) if vol5 else 0
        high20 = float(pd.to_numeric(df["最高"], errors="coerce").tail(20).max()) if len(df)>=20 else float(pd.to_numeric(df["最高"], errors="coerce").max())
        ret5 = (last / float(c.iloc[-6]) - 1) * 100 if len(c)>=6 and c.iloc[-6] else 0
        bias5 = (last / ma5 - 1) * 100 if ma5 else 0
        trend = 0
        if last > ma5: trend += 1
        if ma5 > ma10: trend += 1
        if ma10 > ma20: trend += 1
        breakout = last >= high20 * 0.985
        return {"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"vol_ratio5":round(vol_ratio5,2),"ret5":round(ret5,2),"bias5":round(bias5,2),"trend_level":trend,"near_20d_high":bool(breakout)}
    except Exception:
        return {}


def build_candidates(market: Dict[str, Any], news: Dict[str, Any], hotspot: Dict[str, Any], ak: Any | None = None, limit: int = 12) -> List[Dict[str, Any]]:
    stocks = market.get("active_stocks", []) or []
    if not stocks:
        return []
    hotspot_codes = {str(x.get("code")) for x in hotspot.get("candidates", [])}
    market_score = n((market.get("sentiment") or {}).get("score"), 50)
    stage = str((market.get("sentiment") or {}).get("stage", "中性"))
    board_by_code = {str(x.get("code")): x for x in market.get("limitup_stocks", [])}

    prelim = []
    for s in stocks:
        code = str(s.get("code", "")).zfill(6); name = str(s.get("name", "")); industry = str(s.get("industry", ""))
        if not code or not name or "ST" in name.upper():
            continue
        vp, vp_signals, risks = _volume_price_score(s)
        news_score, news_hits = _topic_match(industry, name, news, hotspot_codes)
        if code in hotspot_codes:
            news_score = max(news_score, 92)
            news_hits.insert(0, "热点链路候选命中")
        board = board_by_code.get(code)
        strength = 35.0
        if board:
            b = n(board.get("board"), 1)
            strength = clamp(55 + min(35, (b-1)*9) + min(10, n(board.get("seal_amount"))/5e8*10))
            if b >= 3: risks.append(f"{int(b)}连板高位")
        else:
            pct = n(s.get("pct")); vr=n(s.get("volume_ratio"))
            strength = clamp(35 + max(0,pct)*3 + min(vr,3)*5)
        # 情绪分作为环境过滤器，不让弱市里所有个股虚高。
        env = clamp(market_score * (1.02 if n(s.get("pct")) > 0 else 0.88))
        total = vp*0.40 + news_score*0.25 + env*0.20 + strength*0.15
        if news_score < 20:
            total -= 5
        prelim.append({**s,"code":code,"volume_price_score":round(vp,1),"news_score":round(news_score,1),"market_score":round(env,1),"strength_score":round(strength,1),"score":round(clamp(total),1),"signals":vp_signals+news_hits,"risks":list(dict.fromkeys(risks)),"stage":stage})

    prelim.sort(key=lambda x: (x["score"], n(x.get("amount"))), reverse=True)
    top = prelim[:max(limit, 16)]
    # 对最靠前的少量标的补历史 K 线特征，失败不影响主流程。
    if ak is not None:
        for item in top[:10]:
            feat = _history_features(ak, item["code"])
            item["history"] = feat
            if feat:
                bonus = 0.0
                if feat.get("trend_level",0) >= 3: bonus += 4
                if feat.get("near_20d_high"): bonus += 3
                if 1.1 <= n(feat.get("vol_ratio5")) <= 2.8: bonus += 3
                if abs(n(feat.get("bias5"))) > 8:
                    bonus -= 4; item["risks"].append("偏离MA5较大")
                if n(feat.get("ret5")) > 28:
                    bonus -= 4; item["risks"].append("5日涨幅较大")
                item["score"] = round(clamp(item["score"] + bonus),1)
                if feat.get("trend_level",0) >= 3: item["signals"].append("MA5>MA10>MA20")
                if feat.get("near_20d_high"): item["signals"].append("接近20日新高")
    top.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(top[:limit], 1):
        item["rank"] = i
        if item["score"] >= 78: label = "重点研究"
        elif item["score"] >= 68: label = "跟踪"
        else: label = "观察"
        item["label"] = label
        item["reason"] = "；".join(item["signals"][:3]) if item["signals"] else "量价与热点综合筛选"
    return top[:limit]
