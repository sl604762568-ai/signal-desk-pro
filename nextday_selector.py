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
    if price>ma5>ma10>ma20: score+=28; why.append("MA5>MA10>MA20")
    elif price>ma20: score+=17; why.append("站上MA20")
    if ma20>ma60: score+=13; why.append("MA20>MA60")
    if dif>dea and hist>0: score+=25; why.append("MACD零轴上方多头")
    elif dif>dea: score+=17; why.append("MACD向上/金叉")
    else: risks.append("MACD尚未确认转强")
    if 45<=rsi<=70: score+=12; why.append(f"RSI {rsi:.0f}")
    elif rsi>78: risks.append("RSI偏热")
    if 1.1<=vr<=2.8: score+=12; why.append(f"量能{vr:.2f}×")
    elif vr>3.5: score+=5; risks.append("量能过热")
    high20=n(x.high.tail(20).max())
    if price>=high20*.97: score+=10; why.append("接近20日高位")
    return {"score":clamp(score),"why":why[:5],"risks":risks,"tech":{"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"ma60":round(ma60,2),"dif":round(dif,4),"dea":round(dea,4),"macd":round(hist,4),"rsi14":round(rsi,1),"vol_ratio5":round(vr,2)}}

def build_next5(market: Dict[str,Any], news: Dict[str,Any], history_fetcher, limit:int=5) -> Dict[str,Any]:
    macro=macro_context(news)
    mscore=n((market.get("sentiment") or {}).get("score"),50)
    mstage=str((market.get("sentiment") or {}).get("stage","中性"))
    stocks=market.get("review_universe") or market.get("active_stocks") or []
    pool=[]
    for s in stocks:
        code=str(s.get("code",'')).zfill(6); name=str(s.get("name",''))
        if len(code)!=6 or code.startswith("688") or "ST" in name.upper() or name.startswith("退"): continue
        pct=n(s.get("pct")); amt=n(s.get("amount")); tr=n(s.get("turnover_rate")); vr=n(s.get("volume_ratio"))
        if amt<8e7: continue
        pre=math.log10(max(amt,1e8))*4 + min(abs(pct),10)*2 + min(tr,20)*.6 + min(vr,4)*3
        pool.append((pre,s))
    pool=[s for _,s in sorted(pool,key=lambda z:z[0],reverse=True)[:64]]

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
        sector,sector_why=sector_score(s,market,news); tech=_technical(df)
        pct=n(s.get("pct")); vr=n(s.get("volume_ratio")); tr=n(s.get("turnover_rate")); amt=n(s.get("amount"))
        vp=45
        vp += 12 if 1.2<=vr<=3.2 else 4 if vr>0 else 0
        vp += 12 if 2<=tr<=15 else 5
        vp += 12 if 0.5<=pct<=7.8 else 4
        vp += 10 if amt>=5e8 else 5
        vp=clamp(vp)
        event=sector # event/news already reflected in sector score; keep separate with direct mention bonus
        market_component=clamp(mscore*.72+macro["score"]*.28)
        total=tech["score"]*.30+sector*.22+vp*.20+event*.13+market_component*.15
        risks=list(tech["risks"])
        if macro["score"]<43: risks.append("海外/宏观事件风险偏高")
        if mscore<45: risks.append("A股市场情绪偏弱")
        if pct>8.5: risks.append("当日涨幅较大，次日追高风险高")
        analysis=analyze_stock(df,code=str(s.get("code",'')).zfill(6),name=str(s.get("name",'')),industry=str(s.get("industry",'')),event_hits=sector_why)
        picks.append({"code":str(s.get("code",'')).zfill(6),"name":s.get("name"),"industry":s.get("industry",''),"price":n(s.get("price")),"pct":pct,"amount":amt,"turnover_rate":tr,"volume_ratio":vr,"score":round(clamp(total),1),"market_score":round(market_component,1),"sector_score":round(sector,1),"volume_price_score":round(vp,1),"technical_score":round(tech["score"],1),"technical":tech["tech"],"reasons":sector_why+tech["why"],"risks":risks[:4],"support":analysis.get("support"),"resistance":analysis.get("resistance"),"chan_signals":(analysis.get("chan") or {}).get("signals",[])})
    picks.sort(key=lambda x:(x["score"],x["amount"]),reverse=True)
    out=[]; industries={}
    for p in picks:
        ind=p.get("industry") or "其他"
        # avoid all five from the same industry unless the market is extremely concentrated
        if industries.get(ind,0)>=2: continue
        industries[ind]=industries.get(ind,0)+1
        out.append(p)
        if len(out)>=limit: break
    for i,p in enumerate(out,1): p["rank"]=i
    env={"market_score":round(mscore,1),"market_stage":mstage,"macro":macro,"decision":"积极研究" if mscore>=62 and macro["score"]>=50 else "控制节奏" if mscore>=45 and macro["score"]>=43 else "偏防守/观察"}
    return {"environment":env,"picks":out,"scanned":len(rows),"universe":len(pool),"excluded":"已排除688开头、ST/退市标记及低流动性股票","note":"5只股票是规则引擎生成的次日研究候选，不是买入指令；开盘后仍需核对指数、板块强弱、竞价和实际成交。"}
