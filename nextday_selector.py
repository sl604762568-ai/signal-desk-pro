from __future__ import annotations
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import pandas as pd
from stock_analysis import enrich_indicators, analyze_stock


def n(v,d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except Exception:return d

def clamp(v): return max(0,min(100,float(v)))

def band_score(x: float, lo: float, sweet_lo: float, sweet_hi: float, hi: float) -> float:
    """0-100 score that rewards being in a practical short-term band, not simply being larger."""
    if x <= 0: return 0.0
    if x < lo or x > hi: return 0.0
    if sweet_lo <= x <= sweet_hi: return 100.0
    if x < sweet_lo:
        return 40 + 60 * (x-lo) / max(1e-9, sweet_lo-lo)
    return 40 + 60 * (hi-x) / max(1e-9, hi-sweet_hi)

MACRO_NEG=["美联储加息","加息预期","CPI超预期","PPI超预期","美股大跌","纳斯达克大跌","标普大跌","美债收益率上行","美元走强","流动性收紧","地缘冲突升级","关税升级"]
MACRO_POS=["美联储降息","降息预期升温","美股上涨","纳指上涨","标普上涨","通胀回落","流动性宽松","美元走弱","美债收益率回落"]

def macro_context(news: Dict[str,Any]) -> Dict[str,Any]:
    score=50.0; hits=[]
    for item in (news.get("items") or [])[:120]:
        t=str(item.get("title",''))
        neg=[w for w in MACRO_NEG if w in t]; pos=[w for w in MACRO_POS if w in t]
        if neg:
            score-=min(8,2.5*len(neg)); hits.append({"direction":"risk","title":t,"why":" / ".join(neg)})
        if pos:
            score+=min(7,2.2*len(pos)); hits.append({"direction":"support","title":t,"why":" / ".join(pos)})
    score=clamp(score)
    level="偏友好" if score>=60 else "偏谨慎" if score<43 else "中性"
    return {"score":round(score,1),"level":level,"hits":hits[:6],"note":"宏观/海外风险由财经新闻事件代理识别，不等同于实时美股指数、美元或利率报价。"}

def sector_score(s: Dict[str,Any], market: Dict[str,Any], news: Dict[str,Any]) -> tuple[float,List[str]]:
    industry=str(s.get("industry",'')); name=str(s.get("name",'')); score=35.0; why=[]
    for th in (market.get("themes") or [])[:16]:
        tn=str(th.get("name",'')); ts=n(th.get("score"),50)
        if tn and (tn in industry or industry in tn):
            score=max(score,ts); why.append(f"板块{tn}强度{ts:.0f}")
    for c in (news.get("clusters") or [])[:14]:
        topic=str(c.get("topic",'')); heat=n(c.get("heat")); direction=str(c.get("direction",''))
        aliases={"AI算力":["通信","电子","计算机","CPO"],"机器人":["机械","自动化","机器人","电机"],"电力电网":["电力","电力设备","电网"],"有色资源":["有色","贵金属","小金属","矿业"]}.get(topic,[])
        if topic in industry or any(a in industry for a in aliases):
            adj=heat + (5 if direction=="偏正" else -7 if direction=="偏负" else 0)
            score=max(score,adj); why.append(f"{topic}新闻热度{heat:.0f}·{direction}")
    direct=[x for x in (news.get("items") or [])[:120] if name and name in str(x.get("title",''))]
    if direct: score=max(score,82+min(12,len(direct)*3)); why.append(f"新闻直接提及{len(direct)}次")
    return clamp(score),why[:3]

def _technical(df: pd.DataFrame) -> Dict[str,Any]:
    x=enrich_indicators(df.tail(100)); last=x.iloc[-1]
    price=n(last.close); ma5=n(last.ma5); ma10=n(last.ma10); ma20=n(last.ma20); ma60=n(last.ma60)
    dif=n(last.dif); dea=n(last.dea); hist=n(last.macd); rsi=n(last.rsi14,50); vr=n(last.vol_ratio5,1)
    score=0; why=[]; risks=[]
    if price>ma5>ma10>ma20: score+=24; why.append("MA5>MA10>MA20")
    elif price>ma20: score+=14; why.append("站上MA20")
    if ma20>ma60: score+=10; why.append("MA20>MA60")
    if dif>dea and hist>0: score+=24; why.append("MACD多头")
    elif dif>dea: score+=16; why.append("MACD向上/金叉")
    else: risks.append("MACD尚未确认转强")
    if 48<=rsi<=68: score+=12; why.append(f"RSI {rsi:.0f}")
    elif 40<=rsi<48 or 68<rsi<=75: score+=6
    elif rsi>78: risks.append("RSI偏热")
    # Relative volume is a ratio: reward moderate expansion, not absolute volume or price.
    if 1.20<=vr<=2.60: score+=18; why.append(f"量比{vr:.2f}×")
    elif 1.05<=vr<1.20 or 2.60<vr<=3.20: score+=10
    elif vr>3.50: score+=4; risks.append("量能过热")
    high20=n(x.high.tail(20).max()); low20=n(x.low.tail(20).min())
    if price>=high20*.965: score+=12; why.append("接近20日高位")
    pos=(price-low20)/max(1e-9,high20-low20) if high20>low20 else .5
    if .55<=pos<=.92: score+=6
    return {"score":clamp(score),"why":why[:6],"risks":risks,"tech":{"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"ma60":round(ma60,2),"dif":round(dif,4),"dea":round(dea,4),"macd":round(hist,4),"rsi14":round(rsi,1),"vol_ratio5":round(vr,2)}}

def _short_term_profile(s: Dict[str,Any], tech: Dict[str,Any]) -> Dict[str,Any]:
    price=n(s.get("price")); pct=n(s.get("pct")); tr=n(s.get("turnover_rate")); amt=n(s.get("amount"));
    vr_snap=n(s.get("volume_ratio")); vr_hist=n((tech.get("tech") or {}).get("vol_ratio5")); vr=vr_snap if vr_snap>0 else vr_hist
    float_cap=n(s.get("float_market_cap")); total_cap=n(s.get("market_cap"))
    float_cap_yi=float_cap/1e8 if float_cap>0 else 0
    total_cap_yi=total_cap/1e8 if total_cap>0 else 0

    # Short-term elasticity prefers moderate float cap / turnover / relative volume.
    cap_s = band_score(float_cap_yi, 12, 25, 120, 260) if float_cap_yi>0 else 55
    amt_yi=amt/1e8
    amt_s = band_score(amt_yi, 0.8, 2.0, 18.0, 45.0)
    turn_s = band_score(tr, 1.5, 4.0, 16.0, 28.0)
    vr_s = band_score(vr, 0.9, 1.25, 2.8, 4.2)
    pct_s = band_score(pct, -1.0, 1.2, 6.8, 9.3)
    elasticity=clamp(cap_s*.28+amt_s*.20+turn_s*.24+vr_s*.18+pct_s*.10)

    risks=[]; why=[]
    if float_cap_yi>0:
        why.append(f"流通市值{float_cap_yi:.0f}亿")
        if float_cap_yi>260: risks.append("流通市值偏大，短线弹性可能不足")
        elif float_cap_yi<12: risks.append("流通市值过小，波动和流动性风险更高")
    why += [f"换手{tr:.1f}%", f"量比{vr:.2f}×", f"成交额{amt_yi:.1f}亿"]
    if tr<1.5: risks.append("换手偏低")
    if tr>28: risks.append("换手过热")
    if vr>4.2: risks.append("量比过热")
    if amt_yi>45: risks.append("成交额过大，偏大票资金结构")
    if price>80: risks.append("股价较高，不符合当前超短偏好")
    return {"score":elasticity,"cap_score":cap_s,"amount_score":amt_s,"turnover_score":turn_s,"volume_ratio_score":vr_s,"pct_score":pct_s,"vr":vr,"float_cap_yi":float_cap_yi,"total_cap_yi":total_cap_yi,"why":why,"risks":risks}

def build_next5(market: Dict[str,Any], news: Dict[str,Any], history_fetcher, limit:int=5) -> Dict[str,Any]:
    macro=macro_context(news)
    mscore=n((market.get("sentiment") or {}).get("score"),50)
    mstage=str((market.get("sentiment") or {}).get("stage","中性"))
    stocks=market.get("review_universe") or market.get("active_stocks") or []
    pool=[]
    for s in stocks:
        code=str(s.get("code",'')).zfill(6); name=str(s.get("name",'')); price=n(s.get("price"))
        if len(code)!=6 or code.startswith("688") or "ST" in name.upper() or name.startswith("退"): continue
        # User's short-term preference: avoid very high-priced names, but price itself never gets a positive score.
        if price<=2.5 or price>80: continue
        pct=n(s.get("pct")); amt=n(s.get("amount")); tr=n(s.get("turnover_rate")); cap=n(s.get("float_market_cap"))/1e8
        if amt<8e7: continue
        if cap>0 and not (10<=cap<=320): continue
        # Pre-selection is banded, so huge turnover/amount no longer dominates.
        pre = band_score(amt/1e8,.8,2,18,45)*.30 + band_score(tr,1,4,16,28)*.35 + band_score(pct,-1,1,7,9.5)*.20 + (band_score(cap,10,25,120,320) if cap>0 else 55)*.15
        pool.append((pre,s))
    pool=[s for _,s in sorted(pool,key=lambda z:z[0],reverse=True)[:72]]

    rows=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs={ex.submit(history_fetcher,str(s.get("code",'')).zfill(6),100):s for s in pool}
        for fut in as_completed(jobs):
            s=jobs[fut]
            try:
                df,_=fut.result()
                if df is not None and len(df)>=60: rows.append((s,df))
            except Exception: pass
    picks=[]
    for s,df in rows:
        sector,sector_why=sector_score(s,market,news); tech=_technical(df); st=_short_term_profile(s,tech)
        pct=n(s.get("pct")); tr=n(s.get("turnover_rate")); amt=n(s.get("amount"))
        event=sector
        market_component=clamp(mscore*.72+macro["score"]*.28)
        # The next-day model now centers on sector/event + short-term elasticity + technical confirmation.
        total=sector*.22 + event*.10 + st["score"]*.26 + tech["score"]*.27 + market_component*.15
        risks=list(tech["risks"])+list(st["risks"])
        if macro["score"]<43: risks.append("海外/宏观事件风险偏高")
        if mscore<45: risks.append("A股市场情绪偏弱")
        if pct>8.0: risks.append("当日涨幅较大，次日追高风险高")
        analysis=analyze_stock(df,code=str(s.get("code",'')).zfill(6),name=str(s.get("name",'')),industry=str(s.get("industry",'')),event_hits=sector_why)
        picks.append({
            "code":str(s.get("code",'')).zfill(6),"name":s.get("name"),"industry":s.get("industry",''),
            "price":n(s.get("price")),"pct":pct,"amount":amt,"turnover_rate":tr,"volume_ratio":round(st["vr"],2),
            "float_market_cap_yi":round(st["float_cap_yi"],1),"market_cap_yi":round(st["total_cap_yi"],1),
            "score":round(clamp(total),1),"market_score":round(market_component,1),"sector_score":round(sector,1),
            "volume_price_score":round(st["score"],1),"short_term_elasticity":round(st["score"],1),"technical_score":round(tech["score"],1),
            "technical":tech["tech"],"reasons":sector_why+st["why"]+tech["why"],"risks":list(dict.fromkeys(risks))[:5],
            "support":analysis.get("support"),"resistance":analysis.get("resistance"),"chan_signals":(analysis.get("chan") or {}).get("signals",[])
        })
    # Do not use amount as a tie-breaker anymore; favor elasticity and technical confirmation.
    picks.sort(key=lambda x:(x["score"],x["short_term_elasticity"],x["technical_score"]),reverse=True)
    out=[]; industries={}
    for p in picks:
        ind=p.get("industry") or "其他"
        if industries.get(ind,0)>=2: continue
        industries[ind]=industries.get(ind,0)+1
        out.append(p)
        if len(out)>=limit: break
    for i,p in enumerate(out,1): p["rank"]=i
    env={"market_score":round(mscore,1),"market_stage":mstage,"macro":macro,"decision":"积极研究" if mscore>=62 and macro["score"]>=50 else "控制节奏" if mscore>=45 and macro["score"]>=43 else "偏防守/观察"}
    return {
        "environment":env,"picks":out,"scanned":len(rows),"universe":len(pool),
        "excluded":"已排除688开头、ST/退市、股价>80元、低流动性；有流通市值数据时优先保留约10~320亿区间。",
        "model_note":"股价绝对值不参与正向评分；量价使用量比、相对均量、换手与成交额区间，流通市值用于短线弹性过滤。",
        "note":"5只股票是规则引擎生成的次日研究候选，不是买入指令；开盘后仍需核对指数、板块强弱、竞价和实际成交。"
    }
