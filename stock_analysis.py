from __future__ import annotations
from typing import Any, Dict, List, Optional
import pandas as pd


def n(v: Any, d: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return d
        return float(v)
    except Exception:
        return d


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50)


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add a compact but broad technical indicator set used by both screening and stock detail.

    Indicators are descriptive research features, not standalone trading instructions.
    """
    x = df.copy().reset_index(drop=True)
    for c in ["open", "close", "high", "low", "volume", "amount"]:
        x[c] = pd.to_numeric(x.get(c), errors="coerce").fillna(0.0)
    close, high, low, volume = x["close"], x["high"], x["low"], x["volume"]
    for p in (5, 10, 20, 60):
        x[f"ma{p}"] = close.rolling(p).mean()
    x["ema12"] = _ema(close, 12); x["ema26"] = _ema(close, 26)
    dif = x["ema12"] - x["ema26"]; dea = _ema(dif, 9)
    x["dif"] = dif; x["dea"] = dea; x["macd"] = (dif - dea) * 2
    x["rsi14"] = _rsi(close, 14)
    # BOLL
    x["boll_mid"] = close.rolling(20).mean()
    boll_std = close.rolling(20).std(ddof=0)
    x["boll_up"] = x["boll_mid"] + 2 * boll_std; x["boll_low"] = x["boll_mid"] - 2 * boll_std
    x["boll_width"] = (x["boll_up"] - x["boll_low"]) / x["boll_mid"].replace(0, pd.NA) * 100
    # KDJ(9,3,3)
    ll9 = low.rolling(9).min(); hh9 = high.rolling(9).max()
    rsv = (close - ll9) / (hh9 - ll9).replace(0, pd.NA) * 100
    x["kdj_k"] = rsv.ewm(alpha=1/3, adjust=False).mean().fillna(50)
    x["kdj_d"] = x["kdj_k"].ewm(alpha=1/3, adjust=False).mean().fillna(50)
    x["kdj_j"] = 3 * x["kdj_k"] - 2 * x["kdj_d"]
    # ATR / directional trend strength (ADX approximation)
    prev_close = close.shift(1)
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean()
    up_move = high.diff(); down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_sum = tr.rolling(14).sum().replace(0, pd.NA)
    plus_di = 100 * plus_dm.rolling(14).sum() / atr_sum; minus_di = 100 * minus_dm.rolling(14).sum() / atr_sum
    dx = ((plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, pd.NA) * 100)
    x["adx14"] = dx.rolling(14).mean(); x["plus_di"] = plus_di; x["minus_di"] = minus_di
    # CCI / WR
    tp = (high + low + close) / 3
    ma_tp = tp.rolling(14).mean(); md = (tp-ma_tp).abs().rolling(14).mean().replace(0, pd.NA)
    x["cci14"] = (tp-ma_tp) / (0.015 * md)
    hh14 = high.rolling(14).max(); ll14 = low.rolling(14).min()
    x["wr14"] = -100 * (hh14-close) / (hh14-ll14).replace(0, pd.NA)
    # OBV / MFI
    direction = close.diff().fillna(0).apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
    x["obv"] = (volume * direction).cumsum(); x["obv_ma10"] = x["obv"].rolling(10).mean()
    raw_money = tp * volume; tp_delta = tp.diff()
    pos_money = raw_money.where(tp_delta > 0, 0.0).rolling(14).sum(); neg_money = raw_money.where(tp_delta < 0, 0.0).rolling(14).sum().abs()
    money_ratio = pos_money / neg_money.replace(0, pd.NA)
    x["mfi14"] = (100 - 100/(1+money_ratio)).fillna(50)
    # Bias and volume
    for p in (6, 12, 24):
        ma = close.rolling(p).mean(); x[f"bias{p}"] = (close-ma) / ma.replace(0, pd.NA) * 100
    x["vol_ma5"] = volume.rolling(5).mean(); x["vol_ratio5"] = volume / x["vol_ma5"].replace(0, pd.NA)
    return x


def pivots(df: pd.DataFrame, wing: int = 2, min_gap: int = 3) -> List[Dict[str, Any]]:
    """Mechanical swing approximation used for visualization, not canonical Chan adjudication."""
    h = df.high.reset_index(drop=True)
    l = df.low.reset_index(drop=True)
    raw: List[Dict[str, Any]] = []
    for i in range(wing, len(df) - wing):
        hh = float(h.iloc[i])
        ll = float(l.iloc[i])
        if hh >= float(h.iloc[i - wing:i + wing + 1].max()):
            raw.append({"i": i, "type": "H", "price": hh})
        if ll <= float(l.iloc[i - wing:i + wing + 1].min()):
            raw.append({"i": i, "type": "L", "price": ll})
    raw.sort(key=lambda z: (z["i"], 0 if z["type"] == "L" else 1))
    out: List[Dict[str, Any]] = []
    for p in raw:
        if not out:
            out.append(p)
            continue
        last = out[-1]
        if p["type"] == last["type"]:
            better = p["price"] > last["price"] if p["type"] == "H" else p["price"] < last["price"]
            if better:
                out[-1] = p
            continue
        if p["i"] - last["i"] < min_gap:
            continue
        out.append(p)
    return out


def all_centers(pivs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return non-duplicate 3-swing overlap regions as approximate centers."""
    if len(pivs) < 4:
        return []
    swings = []
    for a, b in zip(pivs[:-1], pivs[1:]):
        swings.append({
            "start": a["i"], "end": b["i"],
            "low": min(a["price"], b["price"]),
            "high": max(a["price"], b["price"]),
        })
    centers: List[Dict[str, Any]] = []
    for j in range(len(swings) - 2):
        g = swings[j:j + 3]
        zd = max(x["low"] for x in g)
        zg = min(x["high"] for x in g)
        if zd >= zg:
            continue
        c = {"start": g[0]["start"], "end": g[-1]["end"], "zd": float(zd), "zg": float(zg)}
        if centers:
            prev = centers[-1]
            # Merge heavily overlapping adjacent detections to avoid painting many tiny boxes.
            overlap = min(prev["zg"], c["zg"]) - max(prev["zd"], c["zd"])
            base = max(min(prev["zg"] - prev["zd"], c["zg"] - c["zd"]), 1e-9)
            if c["start"] <= prev["end"] + 2 and overlap / base > 0.55:
                prev["end"] = max(prev["end"], c["end"])
                prev["zd"] = max(prev["zd"], c["zd"])
                prev["zg"] = min(prev["zg"], c["zg"])
                continue
        centers.append(c)
    return centers


def latest_center(pivs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cs = all_centers(pivs)
    return cs[-1] if cs else None


def chan_read(df: pd.DataFrame) -> Dict[str, Any]:
    p = pivots(df)
    centers = all_centers(p)
    center = centers[-1] if centers else None
    signals: List[Dict[str, Any]] = []

    lows = [z for z in p if z["type"] == "L"]
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        hs = [z for z in p if z["type"] == "H" and l1["i"] < z["i"] < l2["i"]]
        if hs:
            hh = max(hs, key=lambda z: z["price"])
            higher = (l2["price"] / max(l1["price"], 1e-9) - 1) * 100
            rebound = (hh["price"] / max(l1["price"], 1e-9) - 1) * 100
            if higher > 0 and rebound >= 5:
                signals.append({
                    "type": "二买观察", "kind": "buy2", "i": l2["i"],
                    "level": round(l2["price"], 2),
                    "text": f"第二低点较前低抬高 {higher:.1f}%，此前反弹 {rebound:.1f}%",
                })

    if center and center["end"] < len(df) - 2:
        after = df.iloc[center["end"] + 1:]
        br = after[after["close"] > center["zg"] * 1.01]
        if not br.empty:
            idx = int(br.index[0])
            post = df.loc[idx:]
            if not post.empty:
                low_idx = int(post["low"].idxmin())
                pl = float(post.loc[low_idx, "low"])
                if pl >= center["zg"] * .985 and float(df.close.iloc[-1]) > center["zg"]:
                    signals.append({
                        "type": "三买观察", "kind": "buy3", "i": low_idx,
                        "level": round(pl, 2),
                        "text": f"离开中枢后最低 {pl:.2f}，仍在中枢上沿 {center['zg']:.2f} 附近/上方",
                    })

    return {
        "pivots": p[-22:],
        "centers": centers[-3:],
        "center": center,
        "signals": signals,
    }



def _aggregate_weekly(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).set_index("date").sort_index()
    if x.empty:
        return pd.DataFrame()
    agg = x.resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "amount": "sum",
    }).dropna(subset=["open", "close"]).reset_index()
    agg["date"] = agg["date"].dt.strftime("%Y-%m-%d")
    return agg


def _trend_lines_from_pivots(pivs: List[Dict[str, Any]], offset: int = 0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for typ, name in (("L", "上升支撑线"), ("H", "下降压力线")):
        pts = [z for z in pivs if z.get("type") == typ]
        if len(pts) < 2:
            continue
        a, b = pts[-2], pts[-1]
        ia, ib = int(a["i"]), int(b["i"])
        if ib <= ia:
            continue
        slope = (float(b["price"]) - float(a["price"])) / (ib - ia)
        # Keep both lines; the UI labels whether slope is rising/falling.
        out.append({
            "name": name, "kind": "support" if typ == "L" else "resistance",
            "start": ia - offset, "end": ib - offset,
            "p1": round(float(a["price"]), 4), "p2": round(float(b["price"]), 4),
            "slope": round(slope, 6),
        })
    return out

def _frame_payload(df: pd.DataFrame, label: str, max_rows: int = 100) -> Dict[str, Any]:
    if df is None or len(df) < 12:
        return {"label": label, "rows": [], "chan": {"pivots": [], "centers": [], "center": None, "signals": [], "trendlines": []}, "summary": "数据不足"}
    x = enrich_indicators(df.copy())
    ch = chan_read(x)
    offset = max(0, len(x) - max_rows)
    rows = []
    for _, r in x.tail(max_rows).iterrows():
        rows.append({k: (str(r[k]) if k == "date" else round(n(r[k]), 4)) for k in ["date", "open", "close", "high", "low", "volume", "amount", "ma5", "ma10", "ma20", "ma60", "dif", "dea", "macd", "rsi14", "boll_mid", "boll_up", "boll_low", "kdj_k", "kdj_d", "kdj_j", "atr14", "adx14", "plus_di", "minus_di", "cci14", "wr14", "obv", "obv_ma10", "mfi14", "bias6", "bias12", "bias24"]})
    def rb(obj: Dict[str, Any]) -> Dict[str, Any]:
        y = dict(obj)
        for key in ("i", "start", "end"):
            if key in y:
                y[key] = int(y[key]) - offset
        return y
    center = ch.get("center")
    chan_out = {
        "pivots": [rb(z) for z in ch.get("pivots", []) if int(z.get("i", 0)) >= offset],
        "centers": [rb(z) for z in ch.get("centers", []) if int(z.get("end", 0)) >= offset],
        "center": rb(center) if center and int(center.get("end", 0)) >= offset else None,
        "signals": [rb(z) for z in ch.get("signals", []) if int(z.get("i", 0)) >= offset],
        "trendlines": [z for z in _trend_lines_from_pivots(ch.get("pivots", []), offset) if z["start"] >= 0],
    }
    last = x.iloc[-1]
    price = n(last.close); ma20 = n(last.ma20); ma60 = n(last.ma60); dif=n(last.dif); dea=n(last.dea)
    state = "偏强" if price > ma20 and ma20 >= ma60 and dif >= dea else "震荡" if price >= ma20 * .98 else "偏弱"
    return {
        "label": label, "rows": rows, "chan": chan_out,
        "summary": f"{label}结构{state}：现价 {price:.2f}，MA20 {ma20:.2f}，MA60 {ma60:.2f}，MACD {'DIF≥DEA' if dif>=dea else 'DIF<DEA'}。",
    }

def _condition(label: str, ok: Optional[bool], detail: str) -> Dict[str, Any]:
    return {"label": label, "ok": ok, "detail": detail}


def analyze_stock(
    df: pd.DataFrame, *, code: str, name: str = "", industry: str = "",
    event_hits: Optional[List[str]] = None, market_score: Optional[float] = None,
    market_stage: str = "", market_context: Optional[Dict[str, Any]] = None,
    sector_relations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if df is None or len(df) < 30:
        return {"error": "历史K线不足，至少需要30个交易日"}

    x = enrich_indicators(df.tail(140))
    last = x.iloc[-1]
    price = n(last.close)
    ma5, ma10, ma20, ma60 = (n(last.ma5), n(last.ma10), n(last.ma20), n(last.ma60))
    dif, dea, macd = n(last.dif), n(last.dea), n(last.macd)
    rsi = n(last.rsi14, 50)
    vr = n(last.vol_ratio5, 1)
    boll_mid, boll_up, boll_low = n(last.boll_mid), n(last.boll_up), n(last.boll_low)
    k, d, j = n(last.kdj_k, 50), n(last.kdj_d, 50), n(last.kdj_j, 50)
    atr, adx = n(last.atr14), n(last.adx14)
    plus_di, minus_di = n(last.plus_di), n(last.minus_di)
    cci, wr, mfi = n(last.cci14), n(last.wr14, -50), n(last.mfi14, 50)
    obv, obv_ma10 = n(last.obv), n(last.obv_ma10)
    bias6, bias12, bias24 = n(last.bias6), n(last.bias12), n(last.bias24)
    ret20 = (price / n(x.close.iloc[-21]) - 1) * 100 if len(x) >= 21 and n(x.close.iloc[-21]) else 0
    h20 = n(x.high.tail(20).max())
    l20 = n(x.low.tail(20).min())
    h60 = n(x.high.tail(60).max()) if len(x) >= 60 else h20

    trend_score = 0
    tech: List[str] = []
    risks: List[str] = []
    if price > ma5 > ma10 > ma20:
        trend_score += 30
        tech.append("MA5>MA10>MA20，多头排列")
    elif price > ma20:
        trend_score += 18
        tech.append("价格位于MA20上方")
    else:
        risks.append("价格位于MA20下方，趋势尚弱")
    if ma20 > ma60:
        trend_score += 18
        tech.append("中期MA20高于MA60")
    if dif > dea and macd > 0:
        trend_score += 22
        tech.append("MACD位于零轴上方且DIF>DEA")
    elif dif > dea:
        trend_score += 14
        tech.append("MACD金叉/向上修复")
    elif macd < 0:
        risks.append("MACD柱为负，动能偏弱")
    if 45 <= rsi <= 72:
        trend_score += 12
        tech.append(f"RSI14={rsi:.1f}，处于相对健康区间")
    elif rsi > 78:
        risks.append(f"RSI14={rsi:.1f}，短线偏热")
    if 1.1 <= vr <= 2.8:
        trend_score += 10
        tech.append(f"量能约为5日均量 {vr:.2f}×")
    elif vr > 3.5:
        risks.append("放量过快，需防冲高回落")
    if price >= h20 * .985:
        trend_score += 8
        tech.append("接近20日阶段高位")
    if boll_mid and price >= boll_mid:
        tech.append(f"BOLL位于中轨上方，上轨 {boll_up:.2f}" if boll_up else "BOLL位于中轨上方")
    if k > d and 25 <= k <= 85:
        trend_score += 4; tech.append(f"KDJ K>D（{k:.1f}/{d:.1f}）")
    elif j > 100:
        risks.append(f"KDJ J={j:.1f}，短线偏热")
    if adx >= 25:
        trend_score += 4; tech.append(f"ADX14={adx:.1f}，趋势强度较高")
    if obv > obv_ma10 and obv_ma10 != 0:
        trend_score += 3; tech.append("OBV位于10日均线上方，量价累积偏正")
    if mfi >= 80:
        risks.append(f"MFI14={mfi:.1f}，资金流指标偏热")
    if abs(bias6) >= 8:
        risks.append(f"BIAS6={bias6:+.1f}%，短期乖离较大")
    trend_score = max(0, min(100, trend_score))

    chan = chan_read(x)
    center = chan.get("center")
    recent_low = n(x.low.tail(10).min())
    recent_high = h20
    supports = [z for z in [ma20, center.get("zg") if center else None, center.get("zd") if center else None, recent_low] if z and z > 0]
    resist = [z for z in [recent_high, h60] if z and z > price * 1.002]
    support = round(max([z for z in supports if z <= price * 1.02], default=recent_low), 2)
    resistance = round(min(resist, default=recent_high), 2)

    if center:
        pos = "中枢上方" if price > center["zg"] else ("中枢内部" if price >= center["zd"] else "中枢下方")
        chan_text = f"最近中枢近似区间 {center['zd']:.2f}–{center['zg']:.2f}，现价位于{pos}。"
    else:
        chan_text = "当前日线未识别到足够稳定的三段重叠中枢。"
    if chan["signals"]:
        chan_text += " " + "；".join(s["type"] + "：" + s["text"] for s in chan["signals"])

    bull_trigger = max(ma5, center.get("zg") if center else ma5)
    bear_trigger = min(support, ma20 if ma20 else support)
    scenarios = [
        {"name": "偏强路径", "trigger": round(bull_trigger, 2), "text": f"若收盘稳定在 {bull_trigger:.2f} 上方，并伴随温和放量且MACD不转弱，结构更有利于继续测试 {resistance:.2f} 附近压力。"},
        {"name": "震荡确认", "trigger": round(support, 2), "text": f"若回踩 {support:.2f} 附近缩量企稳，且不破最近结构低点，可继续观察是否形成二次确认或三买式回踩。"},
        {"name": "结构失效", "trigger": round(bear_trigger, 2), "text": f"若有效跌破 {bear_trigger:.2f} 且放量，当前多头/缠论近似结构需要重判，优先看更低一级支撑而不是继续套用原路径。"},
    ]

    # Reference-style condition board. None means market context not available.
    above_center = None if not center else price >= center["zg"] * .995
    market_ok: Optional[bool] = None if market_score is None else market_score >= 55
    conditions = [
        _condition("个股资格", price > ma20 and trend_score >= 45, f"技术面 {trend_score:.0f}/100，现价 {'>' if price > ma20 else '<='} MA20"),
        _condition("趋势", ma5 > ma10 > ma20 and ma20 >= ma60, "MA5/10/20/60 多头结构" if ma5 > ma10 > ma20 and ma20 >= ma60 else "均线尚未形成完整多头"),
        _condition("强度", ret20 > 0 and price >= h60 * .88, f"20日 {ret20:+.1f}% · 距60日高 {(price / h60 - 1) * 100:+.1f}%" if h60 else f"20日 {ret20:+.1f}%"),
        _condition("量能", .85 <= vr <= 3.0, f"成交量 / 5日均量 = {vr:.2f}×"),
        _condition("位置", above_center, (f"现价 {price:.2f} / 中枢 {center['zd']:.2f}–{center['zg']:.2f}" if center else "未形成稳定中枢")),
        _condition("环境", market_ok, (f"市场情绪 {market_score:.0f} · {market_stage or '未标注'}" if market_score is not None else "需结合首页市场情绪确认")),
    ]

    checks = [
        _condition("均线多头", ma5 > ma10 > ma20, f"MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f}"),
        _condition("MACD动能", dif > dea and macd >= 0, f"DIF {dif:.3f} / DEA {dea:.3f} / 柱 {macd:.3f}"),
        _condition("接近60日高", price >= h60 * .92, f"现价距60日高 {(price / h60 - 1) * 100:+.1f}%" if h60 else "--"),
        _condition("流动性/量能", vr >= .85, f"5日量比 {vr:.2f}×"),
        _condition("站回MA20", price >= ma20, f"现价 {price:.2f} / MA20 {ma20:.2f}"),
        _condition("横盘收敛", (n(x.high.tail(10).max()) / max(n(x.low.tail(10).min()), 1e-9)) < 1.15, "近10日振幅 < 15%"),
        _condition("中枢上沿", above_center, (f"ZG {center['zg']:.2f}" if center else "无有效中枢")),
        _condition("二/三买观察", bool(chan["signals"]), "；".join(s["type"] for s in chan["signals"]) if chan["signals"] else "当前未触发近似二/三买"),
    ]

    rows = []
    for _, r in x.tail(100).iterrows():
        rows.append({k: (str(r[k]) if k == "date" else round(n(r[k]), 4)) for k in ["date", "open", "close", "high", "low", "volume", "amount", "ma5", "ma10", "ma20", "ma60", "dif", "dea", "macd", "rsi14", "boll_mid", "boll_up", "boll_low", "kdj_k", "kdj_d", "kdj_j", "atr14", "adx14", "plus_di", "minus_di", "cci14", "wr14", "obv", "obv_ma10", "mfi14", "bias6", "bias12", "bias24"]})

    # Rebase indices because rows only includes x.tail(100).
    offset = max(0, len(x) - 100)
    def rebased(obj: Dict[str, Any]) -> Dict[str, Any]:
        y = dict(obj)
        for key in ("i", "start", "end"):
            if key in y:
                y[key] = int(y[key]) - offset
        return y
    chan_out = {
        "pivots": [rebased(z) for z in chan["pivots"] if int(z["i"]) >= offset],
        "centers": [rebased(z) for z in chan.get("centers", []) if int(z["end"]) >= offset],
        "center": rebased(center) if center and int(center["end"]) >= offset else None,
        "signals": [rebased(z) for z in chan["signals"] if int(z.get("i", 0)) >= offset],
        "trendlines": [z for z in _trend_lines_from_pivots(chan.get("pivots", []), offset) if z["start"] >= 0],
    }
    weekly_df = _aggregate_weekly(df.tail(700))
    timeframes = {
        "daily": {"label": "日线·小级别", "rows": rows, "chan": chan_out, "summary": "日线用于当前结构与短线节奏确认。"},
        "weekly": _frame_payload(weekly_df, "周线·大级别", 80),
    }

    # Technical indicator matrix for the stock-detail dashboard.
    def card(name_: str, value: str, state: str, note_: str) -> Dict[str, str]:
        return {"name": name_, "value": value, "state": state, "note": note_}
    indicator_cards = [
        card("均线", f"MA5 {ma5:.2f} / MA20 {ma20:.2f}", "strong" if ma5>ma10>ma20 else ("weak" if price<ma20 else "neutral"), "观察短中期排列与价格所在位置"),
        card("MACD", f"DIF {dif:.3f} / DEA {dea:.3f}", "strong" if dif>dea and macd>=0 else ("weak" if dif<dea and macd<0 else "neutral"), "动能与零轴位置联动判断"),
        card("RSI14", f"{rsi:.1f}", "hot" if rsi>=75 else ("weak" if rsi<=35 else "neutral"), ">75偏热，<35偏弱，仅作强弱参考"),
        card("BOLL", f"{boll_low:.2f} / {boll_mid:.2f} / {boll_up:.2f}", "strong" if boll_mid and price>boll_mid else "weak", f"带宽 {n(last.boll_width):.1f}%"),
        card("KDJ", f"K {k:.1f} / D {d:.1f} / J {j:.1f}", "strong" if k>d and j<100 else ("hot" if j>=100 else "neutral"), "关注金叉、钝化与高位超买"),
        card("ADX14", f"{adx:.1f}", "strong" if adx>=25 and plus_di>=minus_di else ("weak" if adx>=25 and plus_di<minus_di else "neutral"), f"+DI {plus_di:.1f} / -DI {minus_di:.1f}"),
        card("ATR14", f"{atr:.2f}", "neutral", f"约占现价 {(atr/max(price,1e-9))*100:.1f}% · 衡量波动而非方向"),
        card("CCI14", f"{cci:.1f}", "hot" if cci>150 else ("weak" if cci<-100 else "neutral"), "极端值提示短期加速/超跌"),
        card("WR14", f"{wr:.1f}", "hot" if wr>-20 else ("weak" if wr<-80 else "neutral"), "接近0偏强热，接近-100偏弱"),
        card("MFI14", f"{mfi:.1f}", "hot" if mfi>=80 else ("weak" if mfi<=25 else "neutral"), "结合价格与成交量观察资金流强弱"),
        card("OBV", "高于均线" if obv>obv_ma10 else "低于均线", "strong" if obv>obv_ma10 else "weak", "量价累积方向与OBV 10日均线比较"),
        card("BIAS", f"6日 {bias6:+.1f}% / 12日 {bias12:+.1f}%", "hot" if abs(bias6)>=8 else "neutral", f"24日 {bias24:+.1f}% · 关注乖离过大"),
    ]

    mc = market_context or {}
    upc, downc = mc.get("up_count"), mc.get("down_count")
    breadth = None
    try:
        if upc is not None and downc is not None and float(upc)+float(downc)>0:
            breadth = float(upc)/(float(upc)+float(downc))*100
    except Exception:
        breadth = None
    mscore = market_score if market_score is not None else mc.get("score")
    market_state = "偏强" if mscore is not None and float(mscore)>=60 else ("偏弱" if mscore is not None and float(mscore)<40 else "中性")
    market_linkage = {
        "score": mscore, "stage": market_stage or str(mc.get("stage") or ""), "state": market_state,
        "breadth": round(breadth,1) if breadth is not None else None,
        "seal_rate": mc.get("seal_rate"), "max_board": mc.get("max_board"), "premium": mc.get("yesterday_premium"),
        "summary": f"大盘环境{market_state}" + (f"，情绪 {float(mscore):.0f}/100（{market_stage or mc.get('stage') or '未标注'}）" if mscore is not None else "") + (f"，上涨占比 {breadth:.1f}%" if breadth is not None else "") + "。个股技术信号需与市场承接共同确认。",
    }
    rels = sector_relations or []
    valid_rels = [r for r in rels if r.get("heat") is not None]
    valid_rels.sort(key=lambda r: float(r.get("heat") or 0), reverse=True)
    hot = valid_rels[:6]
    avg_heat = sum(float(r.get("heat") or 0) for r in hot)/len(hot) if hot else None
    avg_pct = sum(float(r.get("pct") or 0) for r in hot)/len(hot) if hot else None
    topic_state = "共振较强" if avg_heat is not None and avg_heat>=70 and (avg_pct or 0)>0 else ("题材偏弱" if avg_heat is not None and avg_heat<45 else "待确认")
    topic_linkage = {
        "state": topic_state, "avg_heat": round(avg_heat,1) if avg_heat is not None else None,
        "avg_pct": round(avg_pct,2) if avg_pct is not None else None, "top": hot,
        "summary": (f"所属高相关行业/概念平均热度 {avg_heat:.1f}，平均涨幅 {avg_pct:+.2f}%，当前判为{topic_state}。" if avg_heat is not None else "所属题材已有真实关系映射，但暂缺对应板块热度快照。"),
    }
    # Strategy confidence is a research ranking, not a probability of profit.
    market_component = float(mscore) if mscore is not None else 50.0
    topic_component = float(avg_heat) if avg_heat is not None else 45.0
    chan_bonus = min(8.0, 4.0 * len(chan.get("signals", [])))
    risk_penalty = min(18.0, 3.0 * len(risks))
    confidence_score = max(0.0, min(100.0, trend_score * 0.48 + market_component * 0.22 + topic_component * 0.22 + chan_bonus - risk_penalty))
    confidence_level = "高置信候选" if confidence_score >= 75 else ("观察区" if confidence_score >= 60 else "暂缓区")
    composite = {
        "technical_score": round(trend_score,1), "market_state": market_state, "topic_state": topic_state,
        "chan_signals": [x.get("type") for x in chan.get("signals", [])],
        "confidence_score": round(confidence_score,1), "confidence_level": confidence_level,
        "summary": f"技术结构 {trend_score:.0f}/100；大盘{market_state}；题材{topic_state}；策略置信度 {confidence_score:.1f}/100（{confidence_level}）；" + ("缠论近似出现"+"/".join(x.get("type","") for x in chan.get("signals",[])) if chan.get("signals") else "当前未出现二/三买近似信号") + "。评分是多维研究排序，不代表收益概率或确定买点。",
    }

    return {
        "code": code, "name": name, "industry": industry, "price": round(price, 2), "trend_score": round(trend_score, 1),
        "summary": f"当前价格 {price:.2f}；20日涨幅 {ret20:+.1f}%；技术面评分 {trend_score:.0f}/100。",
        "technical": {"ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2), "dif": round(dif, 4), "dea": round(dea, 4), "macd": round(macd, 4), "rsi14": round(rsi, 1), "vol_ratio5": round(vr, 2), "ret20": round(ret20, 2), "high20": round(h20, 2), "low20": round(l20, 2), "high60": round(h60, 2), "boll_mid": round(boll_mid,2), "boll_up": round(boll_up,2), "boll_low": round(boll_low,2), "kdj_k": round(k,1), "kdj_d": round(d,1), "kdj_j": round(j,1), "atr14": round(atr,2), "adx14": round(adx,1), "cci14": round(cci,1), "wr14": round(wr,1), "mfi14": round(mfi,1), "bias6": round(bias6,2), "bias12": round(bias12,2), "bias24": round(bias24,2)},
        "indicator_cards": indicator_cards, "market_linkage": market_linkage, "topic_linkage": topic_linkage, "composite_analysis": composite,
        "signals": tech, "risks": risks, "chan": chan_out, "chan_text": chan_text,
        "chan_conditions": conditions, "signal_checks": checks,
        "support": support, "resistance": resistance, "scenarios": scenarios,
        "events": event_hits or [], "rows": rows, "timeframes": timeframes, "error": None,
        "note": "后续路径为条件情景分析，不是对未来价格的确定预测。缠论部分为机械近似，需人工核对分型、笔、线段和中枢。",
    }
