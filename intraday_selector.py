from __future__ import annotations
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from stock_analysis import enrich_indicators, analyze_stock


def n(v,d=0.0):
    try:return float(v)
    except Exception:return d

def clamp(v):return max(0,min(100,float(v)))

def _chan_text(signals):
    out=[]
    for z in signals or []:
        if isinstance(z,dict):
            t=str(z.get("type") or "")
            txt=str(z.get("text") or z.get("reason") or "")
            out.append((t+("："+txt if txt else "")).strip("："))
        else: out.append(str(z))
    return "；".join(x for x in out if x)

def _theme_maps(close_review:Dict[str,Any]):
    hot={}; role={}
    for i,x in enumerate(close_review.get('theme_today') or []):
        hot[str(x.get('theme') or '')]=max(0,100-i*7)
    for x in close_review.get('rotation3',{}).get('transitions') or []:
        s={'新发酵':96,'增强':92,'延续':80,'分歧':55,'退潮':25}.get(x.get('state'),50)
        hot[str(x.get('theme') or '')]=max(hot.get(str(x.get('theme') or ''),0),s)
    for x in close_review.get('roles') or []:
        role[str(x.get('theme') or '')]=x
    return hot,role

def build_intraday_picks(market:Dict[str,Any], close_review:Dict[str,Any], history_fetcher, sector_context, limit=5):
    stocks=market.get('review_universe') or market.get('active_stocks') or []
    sent=n((market.get('sentiment') or {}).get('score'),50); stage=str((market.get('sentiment') or {}).get('stage','中性'))
    hot,roles=_theme_maps(close_review or {})
    pre=[]
    for s in stocks:
        c=str(s.get('code','')).zfill(6); name=str(s.get('name','')); price=n(s.get('price')); pct=n(s.get('pct'))
        if len(c)!=6 or c.startswith(('688','689')) or 'ST' in name.upper() or price<=2 or price>80:continue
        if pct>=9.3 or pct<-2.5:continue
        tr=n(s.get('turnover_rate')); vr=n(s.get('volume_ratio')); amt=n(s.get('amount'))/1e8
        # prefer active but not already fully accelerated names
        room=clamp(100-abs(pct-3.2)*13)
        act=clamp(min(100,tr*6)+min(40,vr*18)+min(25,amt*2))
        pre.append((room*.55+act*.45,s))
    pre=[s for _,s in sorted(pre,key=lambda z:z[0],reverse=True)[:32]]
    rows=[]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs={ex.submit(history_fetcher,str(s.get('code')).zfill(6),90):s for s in pre}
        for f in as_completed(futs):
            s=futs[f]
            try:
                df,_=f.result()
                if df is not None and len(df)>=30: rows.append((s,df))
            except Exception:pass
    out=[]
    for s,df in rows:
        code=str(s.get('code')).zfill(6); pct=n(s.get('pct')); tr=n(s.get('turnover_rate')); vr=n(s.get('volume_ratio')); amt=n(s.get('amount'))/1e8
        x=enrich_indicators(df.tail(90)); last=x.iloc[-1]
        p=n(last.close); ma5=n(last.ma5); ma10=n(last.ma10); ma20=n(last.ma20); dif=n(last.dif); dea=n(last.dea); macd=n(last.macd)
        technical=0; reasons=[]; risks=[]
        if p>ma5>ma10: technical+=30; reasons.append('收盘/现价维持MA5>MA10短趋势')
        elif p>ma10: technical+=18; reasons.append('价格仍在MA10上方')
        if dif>=dea and macd>=0: technical+=25; reasons.append('MACD处于多头确认区')
        elif dif>dea: technical+=16; reasons.append('MACD动能向上')
        bias=(p/ma20-1)*100 if ma20 else 0
        if -1<=bias<=7: technical+=20; reasons.append(f'相对MA20偏离{bias:.1f}%不过热')
        elif bias>12: risks.append('偏离MA20较大')
        if 1.05<=vr<=2.8: technical+=15; reasons.append(f'量比{vr:.2f}×处于活跃区')
        if 3<=tr<=15: technical+=10; reasons.append(f'换手{tr:.1f}%有活跃度')
        relation={}
        try: relation=sector_context(code) or {}
        except Exception:pass
        memberships=relation.get('memberships') or []
        themes=[str(z.get('name') or '') for z in memberships]
        theme_score=0; theme_hit=''
        for t in themes:
            for k,v in hot.items():
                if k and (k in t or t in k):
                    if v>theme_score:theme_score=v;theme_hit=k
        if not theme_score and s.get('industry') in hot:
            theme_hit=str(s.get('industry'));theme_score=hot[theme_hit]
        if theme_hit: reasons.insert(0,f'三日题材轮动：{theme_hit}处于活跃/延续链')
        room=clamp(100-abs(pct-3.5)*12)
        total=technical*.42+theme_score*.28+room*.16+clamp(sent)*.14
        if pct>7.5:total-=12;risks.append('当日涨幅偏高，尾盘追价空间较小')
        analysis=analyze_stock(df,code=code,name=str(s.get('name','')),industry=str(s.get('industry','')),event_hits=reasons[:3])
        out.append({'code':code,'name':s.get('name'),'industry':s.get('industry',''),'price':n(s.get('price')),'pct':pct,'turnover_rate':tr,'volume_ratio':vr,'amount_yi':round(amt,2),
                    'score':round(clamp(total),1),'confidence_score':round(clamp(total),1),'confidence_level':('高置信候选' if clamp(total)>=75 else '观察区' if clamp(total)>=60 else '暂缓区'),'theme':theme_hit or (themes[0] if themes else str(s.get('industry',''))),'theme_score':round(theme_score,1),'technical_score':round(technical,1),
                    'reasons':reasons[:6],'risks':risks[:4],'support':analysis.get('support'),'resistance':analysis.get('resistance'),'chan_signals':(analysis.get('chan') or {}).get('signals',[]),
                    'theme_context':(f'{theme_hit}进入三日轮动活跃链' if theme_hit else (' / '.join(themes[:3]) if themes else str(s.get('industry','')))),
                    'technical_context':f'MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f}；MACD DIF {dif:.3f} / DEA {dea:.3f}；偏离MA20 {bias:.1f}%',
                    'chan_context':_chan_text((analysis.get('chan') or {}).get('signals',[])[:3]) or '暂无明确二/三买标记',
                    'observation':'重点观察尾盘是否保持量价承接、题材不退潮，且不出现放量冲高回落。'})
    out.sort(key=lambda z:z['score'],reverse=True)
    for i,x in enumerate(out[:limit],1):x['rank']=i
    return {'picks':out[:limit],'market_score':round(sent,1),'market_stage':stage,'scanned':len(rows),'note':'14:30尾盘研究池偏向“尚未涨停、仍有结构空间、题材角色链中有承接”的个股；仅作复盘研究，不代表买入建议。'}
