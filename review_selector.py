from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple
import math
import pandas as pd


def n(v: Any, d: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return d
        return float(v)
    except Exception:
        return d


def clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, float(v)))


def market_regime(market: Dict[str, Any]) -> Dict[str, Any]:
    """Use the dashboard's real-time breadth/sentiment to choose today's review style."""
    s = market.get("sentiment") or {}
    score = n(s.get("score"), 50)
    up_raw, down_raw = market.get("up_count"), market.get("down_count")
    if up_raw is None or down_raw is None:
        breadth = 0.50
    else:
        up, down = n(up_raw), n(down_raw)
        breadth = up / max(1, up + down)
    seal = n(market.get("seal_rate"), 60)
    premium = n(market.get("yesterday_premium"), 0)
    dt = n(market.get("dt_count"), 0)

    regime_score = score * .55 + breadth * 100 * .20 + seal * .15 + clamp(50 + premium * 7) * .10
    if dt >= 25:
        regime_score -= 8
    if regime_score >= 68:
        level = "强"
        preferred = ["平台放量突破", "趋势龙头", "轮动初启", "缩量回踩"]
        note = "情绪与广度偏强，复盘优先寻找趋势延续和有效突破，同时保留低位轮动。"
        weights = {"平台放量突破": 1.15, "趋势龙头": 1.10, "轮动初启": 1.05, "缩量回踩": 1.00}
    elif regime_score >= 48:
        level = "中"
        preferred = ["缩量回踩", "轮动初启", "趋势龙头", "平台放量突破"]
        note = "市场处于中性/分歧环境，优先回踩与轮动，减少对加速突破的追逐。"
        weights = {"缩量回踩": 1.15, "轮动初启": 1.10, "趋势龙头": 1.00, "平台放量突破": .95}
    else:
        level = "弱"
        preferred = ["缩量回踩", "趋势龙头", "轮动初启", "平台放量突破"]
        note = "市场偏弱，结果以观察池为主；提高回踩/防守型结构权重，显著降低突破权重。"
        weights = {"缩量回踩": 1.10, "趋势龙头": .95, "轮动初启": .85, "平台放量突破": .72}
    return {
        "level": level,
        "score": round(clamp(regime_score), 1),
        "preferred": preferred,
        "weights": weights,
        "note": note,
        "breadth": round(breadth * 100, 1),
    }


def _hist_df(history_fetcher, code: str) -> pd.DataFrame:
    try:
        df, _source = history_fetcher(code, 180)
        if df is None or len(df) < 65:
            return pd.DataFrame()
        out = pd.DataFrame({
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce"),
            "amount": pd.to_numeric(df.get("amount", 0), errors="coerce"),
        }).dropna(subset=["close", "high", "low", "volume"])
        return out
    except Exception:
        return pd.DataFrame()

def _signals(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    if df.empty or len(df) < 65:
        return {}
    c, h, l, v = df.close, df.high, df.low, df.volume
    last = float(c.iloc[-1]); prev = float(c.iloc[-2])
    ma5 = float(c.tail(5).mean()); ma10 = float(c.tail(10).mean()); ma20 = float(c.tail(20).mean()); ma60 = float(c.tail(60).mean())
    ma20_5 = float(c.iloc[-25:-5].mean()) if len(c) >= 25 else ma20
    ma60_5 = float(c.iloc[-65:-5].mean()) if len(c) >= 65 else ma60
    v5_prev = float(v.iloc[-6:-1].mean()) if len(v) >= 6 else float(v.tail(5).mean())
    vol_ratio = float(v.iloc[-1] / v5_prev) if v5_prev > 0 else 0
    day_ret = (last / prev - 1) * 100 if prev else 0
    ret20 = (last / float(c.iloc[-21]) - 1) * 100 if len(c) >= 21 and c.iloc[-21] else 0
    high60 = float(h.tail(60).max())
    recent_high_bars = int((h.tail(60).iloc[::-1].values.argmax()))
    box_high = float(h.iloc[-31:-1].max())
    box_low = float(l.iloc[-31:-1].min())
    quiet10 = float(h.iloc[-11:-1].max() / max(1e-9, l.iloc[-11:-1].min())) < 1.15
    nohot5 = not bool(((c.tail(6).pct_change() * 100).tail(5) > 8).any())
    cross20 = prev <= float(c.iloc[-21:-1].tail(20).mean()) and last > ma20

    trend_ok = last > ma20 > ma60 and ma20 > ma20_5 and ma60 > ma60_5
    trend_leader = trend_ok and last >= high60 * .92
    pullback = (ma20 > ma60 and ma20 > ma20_5 and ma60 > ma60_5 and recent_high_bars <= 12
                and l.iloc[-1] <= ma20 * 1.02 and l.iloc[-1] >= ma20 * .96
                and last >= ma20 and last <= ma20 * 1.04 and vol_ratio < .80
                and (last >= df.open.iloc[-1] or last >= prev * .99))
    breakout = (box_high / max(box_low, 1e-9) < 1.20 and last > box_high
                and 1.50 < vol_ratio < 3.50 and 3 < day_ret < 9.5
                and last > ma60 and ma60 > ma60_5)
    rotation = (last > ma60 and ma60 > ma60_5 and quiet10 and nohot5 and cross20 and 1.20 < vol_ratio < 2.50)

    base_metrics = {
        "close": round(last, 2), "day_ret": round(day_ret, 2), "ret20": round(ret20, 2),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "vol_ratio5": round(vol_ratio, 2), "bias20": round((last / ma20 - 1) * 100, 2) if ma20 else 0,
        "near60": round(last / high60 * 100, 1) if high60 else 0,
    }

    out: Dict[str, Dict[str, Any]] = {}
    if trend_leader:
        out["趋势龙头"] = {"base": 76 + min(12, max(0, ret20) * .35), "reasons": ["收盘>MA20>MA60", "MA20/MA60上行", f"距60日高点{100-base_metrics['near60']:.1f}%"], **base_metrics}
    if pullback:
        out["缩量回踩"] = {"base": 82, "reasons": ["近期60日高点在12个交易日内", "回踩MA20附近并收回", f"缩量至5日均量{vol_ratio:.2f}倍"], **base_metrics}
    if breakout:
        out["平台放量突破"] = {"base": 86, "reasons": ["30日平台振幅<20%", "收盘突破前30日高点", f"成交量为5日均量{vol_ratio:.2f}倍"], **base_metrics}
    if rotation:
        out["轮动初启"] = {"base": 78, "reasons": ["MA60仍上行", "10日区间收敛且近5日未加速", "重新站上MA20并温和放量"], **base_metrics}
    return out


def _topic_bonus(stock: Dict[str, Any], news: Dict[str, Any], market: Dict[str, Any]) -> Tuple[float, List[str]]:
    name = str(stock.get("name", "")); industry = str(stock.get("industry", ""))
    bonus = 0.0; why: List[str] = []
    for t in (market.get("themes") or [])[:8]:
        tn = str(t.get("name", ""))
        if tn and industry and (tn in industry or industry in tn):
            sc = n(t.get("score"), 0)
            bonus = max(bonus, min(10, sc / 10))
            why.append(f"行业涨停强度{sc:.0f}")
    for c in (news.get("clusters") or [])[:10]:
        topic = str(c.get("topic", "")); heat = n(c.get("heat"), 0)
        if (topic and topic in industry) or any(k in industry for k in [topic.replace("AI算力", "通信"), topic.replace("电力电网", "电力")]):
            bonus = max(bonus, min(9, heat / 11))
            why.append(f"{topic}新闻热度{heat:.0f}")
    direct = [x for x in (news.get("items") or [])[:120] if name and name in str(x.get("title", ""))]
    if direct:
        bonus += min(6, len(direct) * 2)
        why.append(f"新闻直接提及{len(direct)}次")
    return min(15, bonus), why[:2]



def _pivots(df: pd.DataFrame, wing: int = 2, min_gap: int = 3) -> List[Dict[str, Any]]:
    """Simple fractal pivots used only for a mechanical Chan-like screening layer."""
    h = df.high.reset_index(drop=True); l = df.low.reset_index(drop=True)
    raw: List[Dict[str, Any]] = []
    for i in range(wing, len(df) - wing):
        hh = float(h.iloc[i]); ll = float(l.iloc[i])
        if hh >= float(h.iloc[i-wing:i+wing+1].max()): raw.append({"i":i,"type":"H","price":hh})
        if ll <= float(l.iloc[i-wing:i+wing+1].min()): raw.append({"i":i,"type":"L","price":ll})
    raw.sort(key=lambda x:(x["i"], 0 if x["type"]=="L" else 1))
    out: List[Dict[str, Any]] = []
    for p in raw:
        if not out:
            out.append(p); continue
        last = out[-1]
        if p["type"] == last["type"]:
            better = p["price"] > last["price"] if p["type"] == "H" else p["price"] < last["price"]
            if better: out[-1] = p
            continue
        if p["i"] - last["i"] < min_gap:
            continue
        out.append(p)
    return out


def _latest_center(pivs: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Approximate a Chan central zone from overlap of three consecutive swing ranges."""
    if len(pivs) < 5: return None
    swings=[]
    for a,b in zip(pivs[:-1],pivs[1:]):
        swings.append({"start":a["i"],"end":b["i"],"low":min(a["price"],b["price"]),"high":max(a["price"],b["price"])})
    centers=[]
    for j in range(len(swings)-2):
        g=swings[j:j+3]
        zd=max(x["low"] for x in g); zg=min(x["high"] for x in g)
        if zd < zg:
            centers.append({"start":g[0]["start"],"end":g[-1]["end"],"zd":float(zd),"zg":float(zg)})
    return centers[-1] if centers else None


def _chan_signals(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Mechanical approximations of Chan second/third buy candidates; not strict pen/segment parsing."""
    if df.empty or len(df) < 70: return []
    c=df.close.reset_index(drop=True); h=df.high.reset_index(drop=True); l=df.low.reset_index(drop=True); v=df.volume.reset_index(drop=True)
    pivs=_pivots(df)
    if len(pivs) < 4: return []
    last=float(c.iloc[-1]); ma5=float(c.tail(5).mean()); ma20=float(c.tail(20).mean()); ma60=float(c.tail(60).mean())
    v5=float(v.iloc[-6:-1].mean()) if len(v)>=6 else float(v.tail(5).mean())
    vr=float(v.iloc[-1]/v5) if v5>0 else 0
    out=[]

    lows=[p for p in pivs if p["type"]=="L"]
    # 二买近似：前低 -> 反弹高 -> 更高的回踩低点，且当前重新转强。
    if len(lows)>=2:
        l2=lows[-1]; l1=lows[-2]
        highs_between=[p for p in pivs if p["type"]=="H" and l1["i"] < p["i"] < l2["i"]]
        if highs_between:
            hh=max(highs_between,key=lambda x:x["price"])
            rebound=(hh["price"]/max(l1["price"],1e-9)-1)*100
            higher_low=(l2["price"]/max(l1["price"],1e-9)-1)*100
            bars_since=len(df)-1-l2["i"]
            turn_up = last > ma5 and last > float(c.iloc[max(0,len(c)-4):].min())*1.01
            if 0.5 <= higher_low <= 18 and rebound >= 6 and bars_since <= 10 and turn_up and last > ma20*0.98:
                score=82 + min(6,rebound*.18) + (3 if ma20>ma60 else 0) + (2 if vr<=1.4 else 0)
                out.append({
                    "type":"缠论二买候选","score":round(clamp(score),1),
                    "reasons":[f"回踩低点高于前低 {higher_low:.1f}%",f"前段反弹幅度 {rebound:.1f}%",f"距回踩低点 {bars_since} 个交易日","当前重新站上MA5"],
                    "risks":["若后续跌破最近回踩低点，二买近似结构失效"],
                    "metrics":{"first_low":round(l1["price"],2),"pullback_low":round(l2["price"],2),"rebound_high":round(hh["price"],2),"ma20":round(ma20,2),"vol_ratio5":round(vr,2)}
                })

    # 三买近似：形成中枢 -> 向上离开 -> 回踩不有效跌回中枢上沿。
    center=_latest_center(pivs)
    if center and center["end"] < len(df)-2:
        after=df.iloc[center["end"]+1:].reset_index(drop=False)
        if not after.empty:
            breakout_candidates=after[after["close"] > center["zg"]*1.01]
            if not breakout_candidates.empty:
                br_orig=int(breakout_candidates.iloc[0]["index"])
                post=df.iloc[br_orig:]
                pull_low=float(post.low.min()) if not post.empty else 0
                bars_since=len(df)-1-br_orig
                held = pull_low >= center["zg"]*.985 and last > center["zg"] and last > ma5*.98
                leave=(float(df.high.iloc[br_orig:].max())/center["zg"]-1)*100 if center["zg"] else 0
                if held and bars_since <= 15 and leave >= 2:
                    score=85 + min(7,leave*.2) + (2 if ma20>ma60 else 0)
                    out.append({
                        "type":"缠论三买候选","score":round(clamp(score),1),
                        "reasons":[f"中枢约 {center['zd']:.2f}–{center['zg']:.2f}",f"向上离开中枢上沿 {leave:.1f}%",f"离开后最低 {pull_low:.2f}，未有效回到中枢","当前仍在中枢上沿之上"],
                        "risks":[f"若收盘重新跌回中枢上沿 {center['zg']:.2f} 下方，三买近似结构需重判"],
                        "metrics":{"center_low":round(center["zd"],2),"center_high":round(center["zg"],2),"post_low":round(pull_low,2),"leave_pct":round(leave,2),"ma20":round(ma20,2)}
                    })
    return out


def build_review_picks(market: Dict[str, Any], news: Dict[str, Any], history_fetcher=None, limit: int = 10) -> Dict[str, Any]:
    regime = market_regime(market)
    universe = market.get("review_universe") or market.get("active_stocks") or []
    if history_fetcher is None or not universe:
        return {"regime": regime, "picks": [], "chan_picks": [], "scanned": 0, "error": "真实日线数据源不可用，无法执行全市场复盘选股。"}

    # Historical calls are expensive. First rank by liquidity/activity, then fetch a capped pool.
    pool = sorted(universe, key=lambda x: (n(x.get("amount")), abs(n(x.get("pct")))), reverse=True)[:48]
    picks: List[Dict[str, Any]] = []
    chan: List[Dict[str, Any]] = []
    scanned = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    def work(s):
        code=str(s.get("code","")).zfill(6)
        if len(code)!=6 or "ST" in str(s.get("name","")).upper(): return s, pd.DataFrame()
        return s, _hist_df(history_fetcher, code)

    rows=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(work,s) for s in pool]
        for f in as_completed(futs):
            try: rows.append(f.result())
            except Exception: pass

    for s, df in rows:
        if df.empty: continue
        scanned += 1
        code = str(s.get("code", "")).zfill(6)
        topic_bonus, topic_why = _topic_bonus(s, news, market)
        liquidity = min(8, math.log10(max(n(s.get("amount")), 1e8) / 1e8 + 1) * 5)

        found = _signals(df)
        for strategy, sig in found.items():
            weight = n(regime["weights"].get(strategy), 1)
            score = clamp(sig["base"] * weight + topic_bonus + liquidity)
            risks: List[str] = []
            if abs(n(sig.get("bias20"))) > 9: risks.append("偏离MA20较大")
            if n(sig.get("ret20")) > 35: risks.append("20日累计涨幅较高")
            if n(sig.get("vol_ratio5")) > 3.2: risks.append("量能接近过热")
            if regime["level"] == "弱" and strategy == "平台放量突破": risks.append("弱市突破需重点复核失败风险")
            picks.append({
                "code": code, "name": s.get("name"), "industry": s.get("industry", ""),
                "price": n(s.get("price")), "pct": n(s.get("pct")), "amount": n(s.get("amount")),
                "turnover_rate": n(s.get("turnover_rate")), "volume_ratio": n(s.get("volume_ratio")),
                "strategy": strategy, "score": round(score, 1),
                "reasons": sig["reasons"] + topic_why,
                "risks": risks or ["次日仍需核对指数、板块强弱与开盘位置"],
                "metrics": {k: sig[k] for k in ["day_ret", "ret20", "ma5", "ma10", "ma20", "ma60", "vol_ratio5", "bias20", "near60"]},
            })

        for cs in _chan_signals(df):
            # Market regime only modulates confidence; it does not create the signal.
            regime_adj = 3 if regime["level"] == "强" else 0 if regime["level"] == "中" else -5
            score=clamp(n(cs.get("score"))+topic_bonus*.55+liquidity*.35+regime_adj)
            chan.append({
                "code":code,"name":s.get("name"),"industry":s.get("industry",""),"price":n(s.get("price")),"pct":n(s.get("pct")),
                "amount":n(s.get("amount")),"turnover_rate":n(s.get("turnover_rate")),"volume_ratio":n(s.get("volume_ratio")),
                "chan_type":cs["type"],"score":round(score,1),"reasons":cs["reasons"]+topic_why,
                "risks":cs["risks"]+(["当前市场偏弱，结构信号仅作观察"] if regime["level"]=="弱" else []),"metrics":cs["metrics"]
            })

    picks.sort(key=lambda x: (x["score"], x["amount"]), reverse=True)
    dedup=[]; seen=set()
    for p in picks:
        if p["code"] in seen: continue
        seen.add(p["code"]); dedup.append(p)
        if len(dedup)>=limit: break
    for i,p in enumerate(dedup,1): p["rank"]=i

    chan.sort(key=lambda x:(x["score"],x["amount"]),reverse=True)
    cdedup=[]; seen2=set()
    for p in chan:
        key=(p["code"],p["chan_type"])
        if key in seen2: continue
        seen2.add(key); cdedup.append(p)
        if len(cdedup)>=limit: break
    for i,p in enumerate(cdedup,1): p["rank"]=i

    return {"regime": regime, "picks": dedup, "chan_picks": cdedup, "scanned": scanned, "universe": len(pool), "error": None,
            "chan_note":"缠论模块采用日线分型/摆动与中枢重叠的机械近似，用于筛查二买/三买结构候选，不等同于人工严格划笔、线段和中枢。"}
