from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple
import pandas as pd

TOPIC_TO_INDUSTRY={
"机器人":["机器人","自动化","机械","电机","减速器","专用设备"],"AI算力":["通信","光通信","计算机","软件","电子","服务器","数据中心","CPO"],
"半导体":["半导体","元件","电子","芯片"],"新能源车":["汽车","汽车零部件","电池","新能源"],"锂电":["电池","锂电","化工","有色"],
"光伏储能":["光伏","电力设备","新能源","储能"],"电力电网":["电力","电网","电气设备","电力设备"],"有色资源":["有色","贵金属","小金属","矿业","煤炭"],
"军工航天":["军工","航空","航天","船舶","国防"],"医药":["医药","医疗","生物","制药"],"消费":["食品","饮料","家电","零售","旅游","纺织","消费"],
"金融":["证券","银行","保险","金融"],"地产基建":["地产","建筑","建材","水泥","基建"],"农业":["农业","种业","养殖","农牧","食品"]}

def clamp(v,lo=0,hi=100): return max(lo,min(hi,float(v)))
def n(v,d=0.0):
    try:
        if pd.isna(v): return d
        return float(v)
    except Exception:return d

def _topic_match(industry,name,news,hotspot_codes,code):
    hits=[]; score=0.0
    for c in news.get("clusters",[])[:14]:
        topic=str(c.get("topic","")); aliases=TOPIC_TO_INDUSTRY.get(topic,[])
        if topic and (topic in industry or any(a in industry for a in aliases)):
            heat=n(c.get("heat")); score=max(score,heat); hits.append(f"{topic}热度{heat:.0f}")
    direct=[x for x in news.get("items",[])[:120] if name and name in str(x.get("title",""))]
    if direct:
        score=max(score,min(100,78+len(direct)*6)); hits.append(f"新闻直接提及{len(direct)}次")
    if code in hotspot_codes:
        score=max(score,94); hits.insert(0,"热点链路命中")
    return clamp(score),hits[:4]

def _vp(s):
    pct=n(s.get("pct")); vr=n(s.get("volume_ratio")); tr=n(s.get("turnover_rate")); amt=n(s.get("amount")); high=n(s.get("high")); low=n(s.get("low")); price=n(s.get("price")); op=n(s.get("open")); prev=n(s.get("prev_close"))
    sig=[]; risk=[]; score=0
    if 1.2<=vr<=3.5: score+=24; sig.append(f"量比{vr:.2f}×")
    elif .9<=vr<1.2 or 3.5<vr<=5: score+=15
    elif vr>5: score+=10; risk.append("量比过热")
    else: score+=5
    if 3<=tr<=18: score+=22; sig.append(f"换手{tr:.1f}%")
    elif 1.5<=tr<3 or 18<tr<=28: score+=14
    elif tr>28: score+=8; risk.append("换手过高")
    else: score+=5
    if 2<=pct<=8.5: score+=20; sig.append(f"涨幅{pct:+.1f}%")
    elif 0<pct<2 or 8.5<pct<9.8: score+=13
    elif pct>=9.8: score+=10; sig.append("涨停/强封板")
    else: score+=3
    pos=(price-low)/(high-low) if high>low else .5
    if pos>=.78: score+=18; sig.append("日内高位收盘")
    elif pos>=.58: score+=11
    else: score+=4
    if amt>=8e8: score+=16
    elif amt>=3e8: score+=12
    elif amt>=1e8: score+=7
    else: score+=2; risk.append("成交额偏小")
    if prev>0 and op>0 and (op/prev-1)*100>5: risk.append("高开幅度较大")
    return clamp(score),sig[:4],risk[:4]

def _hist(history_fetcher, code):
    try:
        df, _source = history_fetcher(code, 90)
        if df is None or len(df) < 20:
            return {}
        c=pd.to_numeric(df["close"],errors="coerce"); v=pd.to_numeric(df["volume"],errors="coerce"); h=pd.to_numeric(df["high"],errors="coerce")
        last=float(c.iloc[-1]); ma5=float(c.tail(5).mean()); ma10=float(c.tail(10).mean()); ma20=float(c.tail(20).mean()); vol5=float(v.tail(5).mean())
        ret5=(last/float(c.iloc[-6])-1)*100 if len(c)>=6 and c.iloc[-6] else 0; bias5=(last/ma5-1)*100 if ma5 else 0
        trend=sum([last>ma5,ma5>ma10,ma10>ma20]); high20=float(h.tail(20).max())
        return {"ma5":round(ma5,2),"ma10":round(ma10,2),"ma20":round(ma20,2),"vol_ratio5":round(float(v.iloc[-1]/vol5),2) if vol5 else 0,"ret5":round(ret5,2),"bias5":round(bias5,2),"trend_level":trend,"near_20d_high":last>=high20*.985}
    except Exception:return {}

def _bucket(item,board):
    if board and n(board.get("board"),1)>=2:return "连板接力"
    if board:return "首板强势"
    h=item.get("history") or {}
    if h.get("near_20d_high") and h.get("trend_level",0)>=2:return "趋势突破"
    if n(item.get("volume_ratio"))>=1.8 and n(item.get("pct"))>=3:return "放量异动"
    return "强势跟踪"

def build_candidates(market:Dict[str,Any],news:Dict[str,Any],hotspot:Dict[str,Any],history_fetcher=None,limit:int=15):
    stocks=market.get("active_stocks") or []
    if not stocks:return []
    hotspot_codes={str(x.get("code","")).zfill(6) for x in hotspot.get("candidates",[]) if x.get("code")}
    market_score=n((market.get("sentiment") or {}).get("score"),50); stage=str((market.get("sentiment") or {}).get("stage","中性")); board_by={str(x.get("code","")).zfill(6):x for x in market.get("limitup_stocks",[])}
    arr=[]
    for s in stocks:
        code=str(s.get("code","")).zfill(6); name=str(s.get("name","")); industry=str(s.get("industry",''))
        if len(code)!=6 or not name or "ST" in name.upper():continue
        vp,sig,risk=_vp(s); ns,nh=_topic_match(industry,name,news,hotspot_codes,code); board=board_by.get(code)
        if board:
            b=n(board.get("board"),1); strength=clamp(58+min(32,(b-1)*9)+min(10,n(board.get("seal_amount"))/5e8*10)); sig.append(f"{int(b)}板" if b>=2 else "首板")
            if b>=4:risk.append(f"{int(b)}连板高位")
        else:
            strength=clamp(35+max(0,n(s.get("pct")))*3+min(n(s.get("volume_ratio")),3)*5)
        env=clamp(market_score*(1.02 if n(s.get("pct"))>0 else .88)); total=vp*.40+ns*.25+env*.20+strength*.15-(5 if ns<20 else 0)
        arr.append({**s,"code":code,"volume_price_score":round(vp,1),"news_score":round(ns,1),"market_score":round(env,1),"strength_score":round(strength,1),"score":round(clamp(total),1),"signals":sig+nh,"risks":list(dict.fromkeys(risk)),"stage":stage})
    arr.sort(key=lambda x:(x["score"],n(x.get("amount"))),reverse=True); top=arr[:max(limit,20)]
    if history_fetcher is not None:
        for item in top[:12]:
            item["history"]=_hist(history_fetcher,item["code"])
            h=item["history"]
            if h:
                bonus=0
                if h.get("trend_level",0)>=3:bonus+=4; item["signals"].append("MA5>MA10>MA20")
                if h.get("near_20d_high"):bonus+=3; item["signals"].append("接近20日新高")
                if 1.1<=n(h.get("vol_ratio5"))<=2.8:bonus+=3
                if abs(n(h.get("bias5")))>8:bonus-=4; item["risks"].append("偏离MA5较大")
                if n(h.get("ret5"))>28:bonus-=4; item["risks"].append("5日涨幅较大")
                item["score"]=round(clamp(item["score"]+bonus),1)
    top.sort(key=lambda x:x["score"],reverse=True)
    for i,item in enumerate(top[:limit],1):
        board=board_by.get(item["code"]); item["rank"]=i; item["bucket"]=_bucket(item,board)
        h=item.get("history") or {}; item["trend_text"]=("多头排列" if h.get("trend_level",0)>=3 else "趋势改善" if h.get("trend_level",0)>=2 else "趋势待确认") if h else "等待日线核验"
        item["label"]="重点研究" if item["score"]>=78 else "跟踪" if item["score"]>=68 else "观察"
        item["reason"]="；".join(item["signals"][:4]) or "量价与热点综合筛选"
        item["risk_text"]="；".join(list(dict.fromkeys(item["risks"]))[:3]) or "暂无明显结构性风险标签"
    return top[:limit]
