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

def _chan_text(signals):
    out=[]
    for z in signals or []:
        if isinstance(z,dict):
            t=str(z.get("type") or "")
            txt=str(z.get("text") or z.get("reason") or "")
            out.append((t+("："+txt if txt else "")).strip("："))
        else: out.append(str(z))
    return "；".join(x for x in out if x)

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
    industry=str(s.get("industry",'')); name=str(s.get("name",'')); code=str(s.get("code",'')).zfill(6)
    score=32.0; why=[]; labels=[industry]
    try:
        from sector_engine import fetch_stock_sector_info
        info=fetch_stock_sector_info(code) if code and len(code)==6 else {}
        labels += [str(x.get("name") or "") for x in (info.get("concepts") or [])[:12]]
        if info.get("industry") and info.get("industry") not in labels: labels.append(str(info.get("industry")))
    except Exception:
        info={}
    labels=[x for x in labels if x]
    for th in (market.get("themes") or [])[:18]:
        tn=str(th.get("name",'')); ts=n(th.get("score"),50)
        if tn and any(tn in lb or lb in tn for lb in labels):
            score=max(score,ts); why.append(f"板块{tn}强度{ts:.0f}")
    alias_map={"AI算力":["通信","电子","计算机","CPO","液冷服务器","PCB","光模块"],"机器人":["机械","自动化","机器人","电机","减速器"],"电力电网":["电力","电力设备","电网","储能"],"有色资源":["有色","贵金属","小金属","矿业","稀土"],"医药":["医药","创新药","医疗","生物"],"消费电子":["消费电子","苹果","PCB","芯片"]}
    for c in (news.get("clusters") or [])[:18]:
        topic=str(c.get("topic",'')); heat=n(c.get("heat")); direction=str(c.get("direction",''))
        aliases=alias_map.get(topic,[])
        if topic and any(topic in lb or lb in topic or any(a in lb for a in aliases) for lb in labels):
            adj=heat + (8 if direction=="偏正" else -6 if direction=="偏负" else 0)
            score=max(score,adj); why.append(f"{topic}热度{heat:.0f}·{direction or '中性'}")
    direct=[x for x in (news.get("items") or [])[:140] if (name and name in str(x.get("title",''))) or any(lb and lb in str(x.get("title",'')) for lb in labels[:6])]
    if direct:
        score=max(score,72+min(18,len(direct)*2.5))
        why.append(f"新闻/题材直接关联{len(direct)}次")
    if not why and labels:
        why.append('未命中当日主线，暂按板块/概念常规相关性估分')
        score=max(score,45)
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
    ret20=(price/n(x.close.iloc[-21])-1)*100 if len(x)>=21 and n(x.close.iloc[-21]) else 0
    bias20=(price/ma20-1)*100 if ma20 else 0
    high20=n(x.high.tail(20).max()); low20=n(x.low.tail(20).min())
    if price>=high20*.965: score+=12; why.append("接近20日高位")
    pos=(price-low20)/max(1e-9,high20-low20) if high20>low20 else .5
    if .55<=pos<=.92: score+=6
    return {"score":clamp(score),"why":why[:6],"risks":risks,"tech":{"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"ma60":round(ma60,2),"dif":round(dif,4),"dea":round(dea,4),"macd":round(hist,4),"rsi14":round(rsi,1),"vol_ratio5":round(vr,2),"ret20":round(ret20,2),"bias20":round(bias20,2)}}

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

def _real_sector_membership(news: Dict[str,Any], topn: int = 10) -> tuple[Dict[str,List[Dict[str,Any]]], List[Dict[str,Any]], Optional[str]]:
    try:
        from sector_engine import get_sector_heat, fetch_board_members
        sectors=get_sector_heat(news)[:topn]
        stock_map: Dict[str,List[Dict[str,Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(8,max(1,len(sectors)))) as ex:
            jobs={ex.submit(fetch_board_members,s["code"],140):s for s in sectors}
            for fut in as_completed(jobs):
                sec=jobs[fut]
                try: members=fut.result()
                except Exception: continue
                for m in members:
                    code=str(m.get("code","")).zfill(6)
                    stock_map.setdefault(code,[]).append({"code":sec["code"],"name":sec["name"],"heat":sec["heat"],"rank":sec["rank"],"pct":sec["pct"],"type":sec["type"]})
        return stock_map,sectors,None
    except Exception as exc:
        return {},[],f"{type(exc).__name__}: {exc}"

def build_next5(market: Dict[str,Any], news: Dict[str,Any], history_fetcher, limit:int=5) -> Dict[str,Any]:
    macro=macro_context(news)
    mscore=n((market.get("sentiment") or {}).get("score"),50)
    mstage=str((market.get("sentiment") or {}).get("stage","中性"))
    stocks=market.get("review_universe") or market.get("active_stocks") or []
    stock_sector_map, hot_sectors, sector_error = _real_sector_membership(news, topn=6)
    close_review=market.get("close_review") or {}
    rotation_states={str(x.get("theme") or ""):str(x.get("state") or "") for x in ((close_review.get("rotation3") or {}).get("transitions") or [])}
    theme_rank={str(x.get("theme") or ""):max(0,100-i*8) for i,x in enumerate(close_review.get("theme_today") or [])}
    role_themes={str(x.get("theme") or "") for x in (close_review.get("roles") or [])}
    for th in role_themes: theme_rank[th]=max(theme_rank.get(th,0),82)
    market_gate = bool(mscore >= 55 and macro["score"] >= 43)
    pool=[]
    for s in stocks:
        code=str(s.get("code",'')).zfill(6); name=str(s.get("name",'')); price=n(s.get("price"))
        if len(code)!=6 or code.startswith("688") or "ST" in name.upper() or name.startswith("退"): continue
        # 板块先行但不一票否决：高热板块优先，避免真实板块源偶发缺失时不足5只。
        in_hot = (not stock_sector_map) or code in stock_sector_map
        # User's short-term preference: avoid very high-priced names, but price itself never gets a positive score.
        if price<=2.5 or price>80: continue
        pct=n(s.get("pct")); amt=n(s.get("amount")); tr=n(s.get("turnover_rate")); cap=n(s.get("float_market_cap"))/1e8
        if amt<8e7: continue
        if cap>0 and not (10<=cap<=320): continue
        # Pre-selection is banded, so huge turnover/amount no longer dominates.
        sector_pre=max([n(x.get("heat"),50) for x in stock_sector_map.get(code,[])],default=42 if stock_sector_map else 50)
        theme_boost=0
        ind=str(s.get("industry") or "")
        for th,state in rotation_states.items():
            if th and (th in ind or ind in th): theme_boost=max(theme_boost,{"新发酵":20,"增强":18,"延续":12,"分歧":4,"退潮":-12}.get(state,0))
        pre = band_score(amt/1e8,.8,2,18,45)*.22 + band_score(tr,1,4,16,28)*.24 + band_score(pct,-1,1,6.5,9.2)*.14 + (band_score(cap,10,25,120,320) if cap>0 else 55)*.10 + sector_pre*.22 + theme_boost + (4 if in_hot else -4)
        pool.append((pre,s))
    pool=[s for _,s in sorted(pool,key=lambda z:z[0],reverse=True)[:56]]

    rows=[]
    with ThreadPoolExecutor(max_workers=4) as ex:
        jobs={ex.submit(history_fetcher,str(s.get("code",'')).zfill(6),82):s for s in pool}
        for fut in as_completed(jobs):
            s=jobs[fut]
            try:
                df,_=fut.result()
                if df is not None and len(df)>=60: rows.append((s,df))
            except Exception: pass
    picks=[]
    for s,df in rows:
        memberships=stock_sector_map.get(str(s.get("code","")).zfill(6),[])
        if memberships:
            sector=max(n(x.get("heat"),0) for x in memberships)
            sector_why=[f"真实板块：{x.get('name')} 热度{x.get('heat'):.0f}·第{x.get('rank')}" for x in sorted(memberships,key=lambda z:n(z.get("heat")),reverse=True)[:3]]
        else:
            sector,sector_why=sector_score(s,market,news)
        tech=_technical(df); st=_short_term_profile(s,tech)
        pct=n(s.get("pct")); tr=n(s.get("turnover_rate")); amt=n(s.get("amount"))
        event=sector
        market_component=clamp(mscore*.72+macro["score"]*.28)
        # The next-day model now centers on sector/event + short-term elasticity + technical confirmation.
        # 三日题材轮动直接参与评分。
        theme_state=""; theme_bonus=0
        labels=[str(x.get("name") or "") for x in memberships]+[str(s.get("industry") or "")]
        for th,state in rotation_states.items():
            if th and any(th in z or z in th for z in labels if z):
                b={"新发酵":14,"增强":13,"延续":9,"分歧":0,"退潮":-12}.get(state,0)
                if b>theme_bonus: theme_bonus=b; theme_state=f"{th}·{state}"
        for th,rank_score in theme_rank.items():
            if th and any(th in z or z in th for z in labels if z):
                b=rank_score*.10
                if b>theme_bonus: theme_bonus=b; theme_state=theme_state or f"{th}·当日主线"
        ret20=n((tech.get('tech') or {}).get('ret20')); bias20=n((tech.get('tech') or {}).get('bias20'))
        extension_penalty=max(0,pct-6.5)*3.5 + max(0,ret20-22)*0.7 + max(0,bias20-10)*0.8
        early_bonus=5 if -2<=ret20<=18 and -3<=bias20<=8 else 0
        total=sector*.20 + st["score"]*.23 + tech["score"]*.27 + market_component*.16 + theme_bonus + early_bonus - extension_penalty
        risks=list(tech["risks"])+list(st["risks"])
        if macro["score"]<43: risks.append("海外/宏观事件风险偏高")
        if mscore<45: risks.append("A股市场情绪偏弱")
        if pct>8.0: risks.append("当日涨幅较大，次日追高风险高")
        if ret20>22: risks.append(f"20日累计涨幅{ret20:.1f}%偏高，已降低优先级")
        analysis=analyze_stock(df,code=str(s.get("code",'')).zfill(6),name=str(s.get("name",'')),industry=str(s.get("industry",'')),event_hits=sector_why)
        picks.append({
            "code":str(s.get("code",'')).zfill(6),"name":s.get("name"),"industry":s.get("industry",''),
            "price":n(s.get("price")),"pct":pct,"amount":amt,"turnover_rate":tr,"volume_ratio":round(st["vr"],2),
            "float_market_cap_yi":round(st["float_cap_yi"],1),"market_cap_yi":round(st["total_cap_yi"],1),
            "score":round(clamp(total),1),"confidence_score":round(clamp(total),1),"confidence_level":("高置信候选" if clamp(total)>=75 else "观察区" if clamp(total)>=60 else "暂缓区"),"market_score":round(market_component,1),"sector_score":round(sector,1),
            "sector_memberships":memberships[:5],"sector_name":(memberships[0].get("name") if memberships else str(s.get("industry",''))),
            "volume_price_score":round(st["score"],1),"short_term_elasticity":round(st["score"],1),"technical_score":round(tech["score"],1),
            "execution_state":"正式观察" if market_gate else "观察池",
            "technical":tech["tech"],"reasons":([f"三日题材：{theme_state}"] if theme_state else [])+sector_why+st["why"]+tech["why"],"risks":list(dict.fromkeys(risks))[:5],
            "theme_context":theme_state or "未命中三日题材主线，依赖板块/量价独立验证",
            "detailed_reason":f"题材/板块{sector:.0f}分，短线弹性{st['score']:.0f}分，技术面{tech['score']:.0f}分，市场环境{market_component:.0f}分。" + (f" 三日轮动命中{theme_state}。" if theme_state else ""),
            "technical_context":"；".join(tech["why"][:4]) or "技术面等待更多确认",
            "chan_context":_chan_text((analysis.get("chan") or {}).get("signals",[])[:3]) or "暂无明确二/三买标记",
            "support":analysis.get("support"),"resistance":analysis.get("resistance"),"chan_signals":(analysis.get("chan") or {}).get("signals",[])
        })
    # Do not use amount as a tie-breaker anymore; favor elasticity and technical confirmation.
    picks.sort(key=lambda x:(x["score"],x["short_term_elasticity"],x["technical_score"]),reverse=True)
    out=[]; industries={}
    for p in picks:
        ind=p.get("sector_name") or p.get("industry") or "其他"
        if industries.get(ind,0)>=2: continue
        industries[ind]=industries.get(ind,0)+1
        out.append(p)
        if len(out)>=limit: break
    if len(out)<limit:
        existing={x["code"] for x in out}
        for p in picks:
            if p["code"] in existing:continue
            p["secondary_fill"]=True
            p["risks"]=list(dict.fromkeys((p.get("risks") or [])+["用于补足5只观察池，题材集中度/板块共振弱于前排"]))[:5]
            out.append(p);existing.add(p["code"])
            if len(out)>=limit:break
    for i,p in enumerate(out,1): p["rank"]=i
    env={"market_score":round(mscore,1),"market_stage":mstage,"macro":macro,
         "mkt_gate":market_gate,
         "decision":"正式观察" if market_gate and mscore>=62 else "控制节奏" if market_gate else "观察池（MKT门槛未通过）",
         "hot_sectors":[{"code":x.get("code"),"name":x.get("name"),"heat":x.get("heat"),"rank":x.get("rank")} for x in hot_sectors[:8]],
         "sector_relation_source":"真实板块成分关系" if stock_sector_map else "降级：旧行业字段/新闻主题匹配",
         "sector_error":sector_error}
    return {
        "environment":env,"picks":out,"scanned":len(rows),"universe":len(pool),
        "excluded":"已排除688开头、ST/退市、股价>80元、低流动性；有流通市值数据时优先保留约10~320亿区间。",
        "model_note":"三级漏斗：市场环境门槛 → 高热板块真实成分股 → 量价/技术面确认。股价绝对值不参与正向评分；量价使用量比、相对均量、换手与成交额区间。",
        "note":"策略置信度分档：≥75高置信候选，60–74.9观察区，<60暂缓区。评分是规则引擎的多维研究排序，不代表收益概率、确定买点或加仓指令；开盘后仍需核对指数、板块、竞价和实际成交。"
    }
