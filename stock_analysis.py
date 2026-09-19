from __future__ import annotations
from typing import Any, Dict, List, Optional
import math
import pandas as pd


def n(v: Any, d: float = 0.0) -> float:
    try:
        if pd.isna(v): return d
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
    x = df.copy().reset_index(drop=True)
    for c in ["open","close","high","low","volume","amount"]:
        x[c] = pd.to_numeric(x.get(c), errors="coerce").fillna(0.0)
    close = x["close"]
    for p in (5,10,20,60):
        x[f"ma{p}"] = close.rolling(p).mean()
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    x["dif"] = dif
    x["dea"] = dea
    x["macd"] = (dif - dea) * 2
    x["rsi14"] = _rsi(close, 14)
    x["vol_ma5"] = x["volume"].rolling(5).mean()
    x["vol_ratio5"] = x["volume"] / x["vol_ma5"].replace(0, pd.NA)
    return x


def pivots(df: pd.DataFrame, wing: int = 2, min_gap: int = 3) -> List[Dict[str, Any]]:
    h=df.high.reset_index(drop=True); l=df.low.reset_index(drop=True)
    raw=[]
    for i in range(wing, len(df)-wing):
        hh=float(h.iloc[i]); ll=float(l.iloc[i])
        if hh >= float(h.iloc[i-wing:i+wing+1].max()): raw.append({"i":i,"type":"H","price":hh})
        if ll <= float(l.iloc[i-wing:i+wing+1].min()): raw.append({"i":i,"type":"L","price":ll})
    raw.sort(key=lambda z:(z["i"],0 if z["type"]=="L" else 1))
    out=[]
    for p in raw:
        if not out: out.append(p); continue
        last=out[-1]
        if p["type"]==last["type"]:
            better=p["price"]>last["price"] if p["type"]=="H" else p["price"]<last["price"]
            if better: out[-1]=p
            continue
        if p["i"]-last["i"]<min_gap: continue
        out.append(p)
    return out


def latest_center(pivs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(pivs)<5: return None
    swings=[]
    for a,b in zip(pivs[:-1],pivs[1:]):
        swings.append({"start":a["i"],"end":b["i"],"low":min(a["price"],b["price"]),"high":max(a["price"],b["price"])})
    centers=[]
    for j in range(len(swings)-2):
        g=swings[j:j+3]; zd=max(x["low"] for x in g); zg=min(x["high"] for x in g)
        if zd<zg: centers.append({"start":g[0]["start"],"end":g[-1]["end"],"zd":float(zd),"zg":float(zg)})
    return centers[-1] if centers else None


def chan_read(df: pd.DataFrame) -> Dict[str, Any]:
    p=pivots(df)
    center=latest_center(p)
    signals=[]
    lows=[z for z in p if z["type"]=="L"]
    if len(lows)>=2:
        l1,l2=lows[-2],lows[-1]
        hs=[z for z in p if z["type"]=="H" and l1["i"]<z["i"]<l2["i"]]
        if hs:
            hh=max(hs,key=lambda z:z["price"])
            higher=(l2["price"]/max(l1["price"],1e-9)-1)*100
            rebound=(hh["price"]/max(l1["price"],1e-9)-1)*100
            if higher>0 and rebound>=5:
                signals.append({"type":"二买观察","level":round(l2["price"],2),"text":f"第二低点较前低抬高 {higher:.1f}%，此前反弹 {rebound:.1f}%"})
    if center and center["end"]<len(df)-2:
        after=df.iloc[center["end"]+1:]
        br=after[after["close"]>center["zg"]*1.01]
        if not br.empty:
            idx=int(br.index[0]); post=df.loc[idx:]
            pl=float(post.low.min())
            if pl>=center["zg"]*.985 and float(df.close.iloc[-1])>center["zg"]:
                signals.append({"type":"三买观察","level":round(pl,2),"text":f"离开中枢后最低 {pl:.2f}，仍在中枢上沿 {center['zg']:.2f} 附近/上方"})
    return {"pivots":p[-18:],"center":center,"signals":signals}


def analyze_stock(df: pd.DataFrame, *, code: str, name: str = "", industry: str = "", event_hits: Optional[List[str]] = None) -> Dict[str, Any]:
    if df is None or len(df)<30:
        return {"error":"历史K线不足，至少需要30个交易日"}
    x=enrich_indicators(df.tail(140))
    last=x.iloc[-1]; prev=x.iloc[-2]
    price=n(last.close); ma5=n(last.ma5); ma10=n(last.ma10); ma20=n(last.ma20); ma60=n(last.ma60)
    dif=n(last.dif); dea=n(last.dea); macd=n(last.macd); rsi=n(last.rsi14,50); vr=n(last.vol_ratio5,1)
    ret20=(price/n(x.close.iloc[-21])-1)*100 if len(x)>=21 and n(x.close.iloc[-21]) else 0
    h20=n(x.high.tail(20).max()); l20=n(x.low.tail(20).min())
    trend_score=0; tech=[]; risks=[]
    if price>ma5>ma10>ma20: trend_score+=30; tech.append("MA5>MA10>MA20，多头排列")
    elif price>ma20: trend_score+=18; tech.append("价格位于MA20上方")
    else: risks.append("价格位于MA20下方，趋势尚弱")
    if ma20>ma60: trend_score+=18; tech.append("中期MA20高于MA60")
    if dif>dea and macd>0: trend_score+=22; tech.append("MACD位于零轴上方且DIF>DEA")
    elif dif>dea: trend_score+=14; tech.append("MACD金叉/向上修复")
    elif macd<0: risks.append("MACD柱为负，动能偏弱")
    if 45<=rsi<=72: trend_score+=12; tech.append(f"RSI14={rsi:.1f}，处于相对健康区间")
    elif rsi>78: risks.append(f"RSI14={rsi:.1f}，短线偏热")
    if 1.1<=vr<=2.8: trend_score+=10; tech.append(f"量能约为5日均量 {vr:.2f}×")
    elif vr>3.5: risks.append("放量过快，需防冲高回落")
    if price>=h20*.985: trend_score+=8; tech.append("接近20日阶段高位")
    trend_score=max(0,min(100,trend_score))

    chan=chan_read(x)
    center=chan.get("center")
    recent_low=n(x.low.tail(10).min()); recent_high=n(x.high.tail(20).max())
    supports=[z for z in [ma20, center.get("zg") if center else None, center.get("zd") if center else None, recent_low] if z and z>0]
    resist=[z for z in [recent_high, n(x.high.tail(60).max())] if z and z>price*1.002]
    support=round(max([z for z in supports if z<=price*1.02], default=recent_low),2)
    resistance=round(min(resist, default=recent_high),2)

    if center:
        chan_text=f"最近中枢近似区间 {center['zd']:.2f}–{center['zg']:.2f}。"
    else:
        chan_text="当前日线未识别到足够稳定的三段重叠中枢。"
    if chan["signals"]:
        chan_text += " " + "；".join(s["type"]+"："+s["text"] for s in chan["signals"])

    bull_trigger=max(ma5, center.get("zg") if center else ma5)
    bear_trigger=min(support, ma20 if ma20 else support)
    scenarios=[
        {"name":"偏强路径","trigger":round(bull_trigger,2),"text":f"若收盘稳定在 {bull_trigger:.2f} 上方，并伴随温和放量且MACD不转弱，结构更有利于继续测试 {resistance:.2f} 附近压力。"},
        {"name":"震荡确认","trigger":round(support,2),"text":f"若回踩 {support:.2f} 附近缩量企稳，且不破最近结构低点，可继续观察是否形成二次确认或三买式回踩。"},
        {"name":"结构失效","trigger":round(bear_trigger,2),"text":f"若有效跌破 {bear_trigger:.2f} 且放量，当前多头/缠论近似结构需要重判，优先看更低一级支撑而不是继续套用原路径。"},
    ]

    rows=[]
    for _,r in x.tail(100).iterrows():
        rows.append({k:(str(r[k]) if k=="date" else round(n(r[k]),4)) for k in ["date","open","close","high","low","volume","amount","ma5","ma10","ma20","ma60","dif","dea","macd","rsi14"]})
    return {
        "code":code,"name":name,"industry":industry,"price":round(price,2),"trend_score":round(trend_score,1),
        "summary":f"当前价格 {price:.2f}；20日涨幅 {ret20:+.1f}%；技术面评分 {trend_score:.0f}/100。",
        "technical":{"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"ma60":round(ma60,2),"dif":round(dif,4),"dea":round(dea,4),"macd":round(macd,4),"rsi14":round(rsi,1),"vol_ratio5":round(vr,2),"ret20":round(ret20,2),"high20":round(h20,2),"low20":round(l20,2)},
        "signals":tech,"risks":risks,"chan":chan,"chan_text":chan_text,"support":support,"resistance":resistance,
        "scenarios":scenarios,"events":event_hits or [],"rows":rows,"error":None,
        "note":"后续路径为条件情景分析，不是对未来价格的确定预测。缠论部分为机械近似，需人工核对笔、线段和中枢。"
    }
