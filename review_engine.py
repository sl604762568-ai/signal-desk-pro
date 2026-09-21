from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo('Asia/Shanghai')


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _rows(obj: Any) -> List[Dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return [dict(x) for x in obj if isinstance(x, dict)]
    try:
        # pandas DataFrame
        return [dict(x) for x in obj.to_dict('records')]
    except Exception:
        pass
    if isinstance(obj, dict):
        for key in ('data', 'items', 'list', 'rows'):
            if isinstance(obj.get(key), list):
                return [dict(x) for x in obj[key] if isinstance(x, dict)]
    return []


def _code(v: Any) -> str:
    s = re.sub(r'\D', '', str(v or ''))
    return s[-6:].zfill(6) if s else ''


def _time_key(v: Any) -> int:
    s = re.sub(r'\D', '', str(v or ''))
    if not s:
        return 999999
    try:
        return int(s[-6:])
    except Exception:
        return 999999


def _split_themes(v: Any) -> List[str]:
    if isinstance(v, (list, tuple, set)):
        vals = [str(x).strip() for x in v]
    else:
        text = str(v or '').strip()
        if not text:
            return []
        vals = re.split(r'[+＋、/|｜;,；，\s]+', text)
    out=[]
    seen=set()
    for x in vals:
        x=x.strip(' -—·')
        if not x or len(x)>24:
            continue
        # remove boilerplate rather than useful theme names
        if x in {'涨停','首板','二板','三板','四板','五板','六板'}:
            continue
        if x not in seen:
            seen.add(x); out.append(x)
    return out[:8]


def _safe_import_lk():
    try:
        import levistock as lk  # type: ignore
        return lk
    except Exception:
        return None


def _safe_call(lk: Any, name: str, *args, **kwargs):
    if lk is None:
        return None, 'levistock unavailable'
    fn = getattr(lk, name, None)
    if not callable(fn):
        return None, f'{name} unavailable'
    try:
        return fn(*args, **kwargs), None
    except TypeError:
        # tolerate wrappers that changed an optional signature
        try:
            return fn(*args), None
        except Exception as exc:
            return None, f'{type(exc).__name__}: {exc}'
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def _normalize_em_zt(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out=[]
    for r in items:
        code=_code(r.get('stock_code') or r.get('代码') or r.get('code') or r.get('secu_code'))
        name=str(r.get('stock_name') or r.get('名称') or r.get('name') or r.get('secu_name') or '')
        if not code or not name:
            continue
        out.append({
            'code':code,'name':name,
            'price':_num(r.get('price') or r.get('最新价') or r.get('last_px')),
            'pct':_num(r.get('change_pct') or r.get('涨跌幅') or r.get('change')),
            'continuous':max(1,int(_num(r.get('continuous') or r.get('连板数') or r.get('limit_count'),1))),
            'first_zt_time':r.get('first_zt_time') or r.get('首次涨停时间') or r.get('limit_time') or '',
            'last_zt_time':r.get('last_zt_time') or r.get('最后涨停时间') or '',
            'open_times':int(_num(r.get('open_times') or r.get('炸板次数'),0)),
            'amount':_num(r.get('amount') or r.get('成交额') or r.get('turnover')),
            'turnover_rate':_num(r.get('turnover_rate') or r.get('换手率')),
            'main_inflow':_num(r.get('main_inflow') or r.get('主力净流入') or r.get('net_inflow')),
            'sector':str(r.get('sector') or r.get('所属行业') or r.get('industry') or ''),
            'reason':str(r.get('up_reason') or r.get('reason') or r.get('涨停原因') or ''),
            'themes':_split_themes(r.get('themes') or r.get('题材') or r.get('up_reason') or r.get('reason')),
        })
    # deduplicate by code, prefer row with higher continuous / richer fields
    best={}
    for x in out:
        old=best.get(x['code'])
        if not old or (x['continuous'],len(x['reason']),x['amount']) > (old['continuous'],len(old['reason']),old['amount']):
            best[x['code']]=x
    return list(best.values())


def _normalize_cls_reasons(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out={}
    for r in items:
        code=_code(r.get('secu_code') or r.get('stock_code') or r.get('code'))
        if not code:
            continue
        reason=str(r.get('up_reason') or r.get('reason') or '')
        out[code]={
            'reason':reason,
            'themes':_split_themes(reason),
            'name':str(r.get('secu_name') or r.get('stock_name') or r.get('name') or ''),
        }
    return out


def _normalize_kph(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out=[]
    for r in items:
        code=_code(r.get('code') or r.get('stock_code'))
        name=str(r.get('name') or r.get('stock_name') or '')
        if not code or not name:
            continue
        themes=_split_themes(r.get('themes'))
        reason=str(r.get('reason') or '')
        if not themes:
            themes=_split_themes(reason)
        out.append({
            'code':code,'name':name,'continuous':max(1,int(_num(r.get('limit_count'),1))),
            'amount':_num(r.get('turnover')),'turnover_rate':_num(r.get('turnover_rate')),
            'main_inflow':_num(r.get('net_inflow')),'sector':'',
            'reason':reason,'themes':themes,
            'first_zt_time':r.get('limit_time') or '', 'last_zt_time':r.get('limit_time') or '',
            'open_times':1 if _num(r.get('open_time')) else 0,
        })
    return out


def _find_trade_dates(lk: Any, want: int = 3) -> List[str]:
    # Prefer provider trading-calendar helper if available.
    obj, _ = _safe_call(lk, 'get_trade_days', want)
    vals=[]
    if obj:
        if isinstance(obj, (list,tuple)):
            for x in obj:
                s=str(x)[:10]
                if re.match(r'^\d{4}-\d{2}-\d{2}$',s): vals.append(s)
                elif re.match(r'^\d{8}$',str(x)): vals.append(f'{str(x)[:4]}-{str(x)[4:6]}-{str(x)[6:8]}')
        elif hasattr(obj,'tolist'):
            for x in obj.tolist():
                s=str(x)[:10];
                if re.match(r'^\d{4}-\d{2}-\d{2}$',s): vals.append(s)
    if vals:
        return sorted(set(vals))[-want:]

    # Robust fallback: probe recent dates and retain dates with limit-up data.
    got=[]
    today=datetime.now(CN_TZ).date()
    for i in range(0,12):
        d=today-timedelta(days=i)
        if d.weekday()>=5:
            continue
        ds=d.strftime('%Y%m%d')
        obj,_=_safe_call(lk,'stock_zt_pool_em',date=ds)
        if _rows(obj):
            got.append(d.isoformat())
            if len(got)>=want: break
    return list(reversed(got))


def _enrich_current_with_cls(current: List[Dict[str, Any]], cls_reason: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out=[]
    for x in current:
        y=dict(x)
        cr=cls_reason.get(x['code']) or {}
        if cr.get('reason'):
            y['reason']=cr['reason']
        if cr.get('themes'):
            y['themes']=cr['themes']
        if not y.get('themes') and y.get('sector'):
            y['themes']=[y['sector']]
        out.append(y)
    return out


def _theme_key(x: Dict[str, Any]) -> List[str]:
    themes=list(x.get('themes') or [])
    if not themes and x.get('sector'):
        themes=[str(x['sector'])]
    if not themes:
        themes=['其他']
    return themes[:4]


def _theme_aggregate(items: List[Dict[str, Any]], date: str) -> List[Dict[str, Any]]:
    groups=defaultdict(list)
    for x in items:
        for t in _theme_key(x):
            groups[t].append(x)
    rows=[]
    for theme,stocks in groups.items():
        boards=[int(x.get('continuous') or 1) for x in stocks]
        first=sum(1 for b in boards if b==1)
        high=max(boards or [1])
        amount=sum(_num(x.get('amount')) for x in stocks)
        leader=sorted(stocks,key=lambda z:(int(z.get('continuous') or 1),-_time_key(z.get('first_zt_time')),_num(z.get('amount'))),reverse=True)[0]
        score=len(stocks)*12 + high*15 + first*2 + min(15, math.log10(max(amount,1))/10*15)
        rows.append({
            'date':date,'theme':theme,'count':len(stocks),'first_board':first,'max_board':high,
            'amount':round(amount,2),'leader':{'code':leader['code'],'name':leader['name'],'board':leader.get('continuous',1)},
            'score':round(score,1),'stocks':[{'code':z['code'],'name':z['name'],'board':z.get('continuous',1),'reason':z.get('reason','')} for z in sorted(stocks,key=lambda z:(int(z.get('continuous') or 1),_num(z.get('amount'))),reverse=True)[:8]],
        })
    rows.sort(key=lambda x:(x['score'],x['max_board'],x['count']),reverse=True)
    return rows


def _rotation_compare(days: List[Dict[str, Any]]) -> Dict[str, Any]:
    # days chronological: T-2, T-1, T
    if not days:
        return {'days':[],'path':'','transitions':[]}
    maps=[]
    for d in days:
        mp={r['theme']:{**r,'rank':i+1} for i,r in enumerate(d.get('themes') or [])}
        maps.append(mp)
    today=maps[-1]
    prev=maps[-2] if len(maps)>=2 else {}
    prev2=maps[-3] if len(maps)>=3 else {}
    transitions=[]
    names=set(today)|set(prev)|set(prev2)
    for name in names:
        a=prev2.get(name); b=prev.get(name); c=today.get(name)
        if c and not b:
            state='新发酵'
        elif c and b:
            if c['rank'] < b['rank'] or c['count'] > b['count']:
                state='增强'
            elif c['rank']==b['rank'] and c['count']==b['count']:
                state='延续'
            else:
                state='分歧'
        elif b and not c:
            state='退潮'
        else:
            continue
        transitions.append({
            'theme':name,'state':state,
            't2_rank':a.get('rank') if a else None,'t1_rank':b.get('rank') if b else None,'t_rank':c.get('rank') if c else None,
            't2_count':a.get('count') if a else 0,'t1_count':b.get('count') if b else 0,'t_count':c.get('count') if c else 0,
            'max_board':c.get('max_board') if c else (b.get('max_board') if b else 0),
        })
    order={'新发酵':0,'增强':1,'延续':2,'分歧':3,'退潮':4}
    transitions.sort(key=lambda x:(order.get(x['state'],9), x.get('t_rank') or 999))
    leaders=[]
    for d in days:
        top=(d.get('themes') or [])[:1]
        leaders.append(top[0]['theme'] if top else '--')
    return {'days':days,'path':' → '.join(leaders),'transitions':transitions[:18]}


def _promotion_rates(prev: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cur={x['code']:int(x.get('continuous') or 1) for x in current}
    out=[]
    for level in range(1,7):
        base=[x for x in prev if int(x.get('continuous') or 1)==level]
        if not base:
            continue
        hits=[x for x in base if cur.get(x['code'],0)>=level+1]
        out.append({'label':f'{level}→{level+1}','numerator':len(hits),'denominator':len(base),'rate':round(len(hits)/len(base)*100,1),'stocks':[x['name'] for x in hits[:10]]})
    return out


def _roles(current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups=defaultdict(list)
    for x in current:
        # one primary theme to avoid a stock becoming leader in four groups at once
        key=(_theme_key(x) or ['其他'])[0]
        groups[key].append(x)
    out=[]
    for theme,stocks in groups.items():
        if len(stocks)<2:
            continue
        ranked=sorted(stocks,key=lambda z:(int(z.get('continuous') or 1), -_time_key(z.get('first_zt_time')), _num(z.get('amount'))),reverse=True)
        leader=ranked[0]
        by_amount=sorted(stocks,key=lambda z:_num(z.get('amount')),reverse=True)
        middle=next((z for z in by_amount if z['code']!=leader['code']), leader)
        by_time=sorted(stocks,key=lambda z:_time_key(z.get('first_zt_time')))
        intraday=by_time[0]
        firstboards=[z for z in stocks if int(z.get('continuous') or 1)==1]
        assists=[z for z in ranked if z['code'] not in {leader['code'],middle['code'],intraday['code']}][:3]
        back=[z for z in reversed(by_time) if z['code'] not in {leader['code'],middle['code'],intraday['code']}][:3]
        out.append({
            'theme':theme,'count':len(stocks),'max_board':max(int(z.get('continuous') or 1) for z in stocks),
            'first_board_count':len(firstboards),'first_board_wave':len(firstboards)>=3,
            'leader':{'code':leader['code'],'name':leader['name'],'board':leader['continuous'],'reason':leader.get('reason','')},
            'middle':{'code':middle['code'],'name':middle['name'],'board':middle['continuous'],'amount':middle.get('amount',0)},
            'intraday':{'code':intraday['code'],'name':intraday['name'],'time':intraday.get('first_zt_time','')},
            'assists':[{'code':z['code'],'name':z['name'],'board':z['continuous']} for z in assists],
            'back':[{'code':z['code'],'name':z['name'],'board':z['continuous']} for z in back],
        })
    out.sort(key=lambda x:(x['max_board'],x['count'],x['first_board_count']),reverse=True)
    return out[:12]


def _cls_rotation(lk: Any) -> List[Dict[str, Any]]:
    obj,err=_safe_call(lk,'get_sector_rotation',days=3)
    data=_rows(obj)
    out=[]
    if isinstance(obj,list):
        for d in obj:
            if not isinstance(d,dict): continue
            plates=d.get('plates') or []
            out.append({'date':str(d.get('trade_date') or ''),'plates':[{'name':str(p.get('plate_name') or p.get('name') or ''),'code':str(p.get('plate_code') or p.get('code') or ''),'pct':_num(p.get('change') or p.get('change_pct'))} for p in plates[:10] if isinstance(p,dict)]})
    return out


def build_close_review() -> Dict[str, Any]:
    started=time.time()
    lk=_safe_import_lk()
    source_status={}

    # Parallel current-day sources.
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_em=ex.submit(_safe_call,lk,'stock_zt_pool_em')
        fut_dt=ex.submit(_safe_call,lk,'stock_dt_pool_em')
        fut_cls=ex.submit(_safe_call,lk,'stock_zt_pool_cls')
        fut_emotion=ex.submit(_safe_call,lk,'market_emotion_cls')
        fut_kph=ex.submit(_safe_call,lk,'market_emotion_kph')
        fut_resume=ex.submit(_safe_call,lk,'get_his_limit_resumption')
        fut_zttt=ex.submit(_safe_call,lk,'get_zttt')
        fut_ths=ex.submit(_safe_call,lk,'stock_hot_rank_ths',50)
        em_obj,em_err=fut_em.result(); dt_obj,dt_err=fut_dt.result(); cls_obj,cls_err=fut_cls.result(); emo_obj,emo_err=fut_emotion.result(); kph_obj,kph_err=fut_kph.result(); resume_obj,resume_err=fut_resume.result(); zttt_obj,zttt_err=fut_zttt.result(); ths_obj,ths_err=fut_ths.result()

    em=_normalize_em_zt(_rows(em_obj))
    dt=_rows(dt_obj)
    cls_map=_normalize_cls_reasons(_rows(cls_obj))
    current=_enrich_current_with_cls(em,cls_map)

    source_status['eastmoney_limit_pool']={'ok':bool(em),'count':len(em),'error':em_err}
    source_status['eastmoney_limit_down']={'ok':dt_obj is not None,'count':len(dt),'error':dt_err}
    source_status['cls_limit_reason']={'ok':bool(cls_map),'count':len(cls_map),'error':cls_err}
    source_status['cls_emotion']={'ok':emo_obj is not None,'error':emo_err}
    source_status['kph_emotion']={'ok':kph_obj is not None,'error':kph_err}
    source_status['kph_resumption']={'ok':isinstance(resume_obj,dict),'error':resume_err}
    source_status['kph_ladder']={'ok':isinstance(zttt_obj,dict),'error':zttt_err}
    source_status['ths_hot_rank']={'ok':ths_obj is not None,'count':len(_rows(ths_obj)),'error':ths_err}
    # Official exchange endpoints are used as public rule/reference anchors, not as free redistributable tick feeds.
    source_status['exchange_public']={'ok':True,'note':'上交所/深交所公开市场与交易规则用于交易日、证券代码与涨跌幅规则校验；实时逐笔行情仍由公开行情节点交叉验证。'}

    if not current:
        return {'ok':False,'verified':False,'error':'专门涨停池暂不可用；为避免错误，本页不发布推算的涨停/连板数字。','source_status':source_status,'elapsed_ms':int((time.time()-started)*1000)}

    # Cross-check counts. Different sources have slightly different ST definitions, so store both rather than forcing equality.
    cls_count=len(cls_map)
    emo=emo_obj if isinstance(emo_obj,dict) else {}
    kph=kph_obj if isinstance(kph_obj,dict) else {}
    kph_zt=int(_num(kph.get('zt'))) if kph else None
    kph_dt=int(_num(kph.get('dt'))) if kph else None
    resume_nums=(resume_obj.get('nums') or {}) if isinstance(resume_obj,dict) else {}
    resume_zt=int(_num(resume_nums.get('ZT'))) if resume_nums.get('ZT') is not None else None
    resume_dt=int(_num(resume_nums.get('DT'))) if resume_nums.get('DT') is not None else None
    zt_count=len(current); dt_count=len(dt)
    checks=[]
    if cls_count:
        checks.append({'name':'东财 vs 财联社涨停池','a':zt_count,'b':cls_count,'diff':abs(zt_count-cls_count),'pass':abs(zt_count-cls_count)<=max(2,round(zt_count*.04))})
    if kph_zt:
        checks.append({'name':'东财 vs 开盘红涨停总数','a':zt_count,'b':kph_zt,'diff':abs(zt_count-kph_zt),'pass':abs(zt_count-kph_zt)<=max(3,round(zt_count*.05))})
    if kph_dt is not None and dt_obj is not None:
        checks.append({'name':'东财 vs 开盘红跌停总数','a':dt_count,'b':kph_dt,'diff':abs(dt_count-kph_dt),'pass':abs(dt_count-kph_dt)<=2})
    if resume_zt is not None:
        checks.append({'name':'东财 vs 开盘红涨停复盘','a':zt_count,'b':resume_zt,'diff':abs(zt_count-resume_zt),'pass':abs(zt_count-resume_zt)<=max(2,round(zt_count*.04))})
    if resume_dt is not None and dt_obj is not None:
        checks.append({'name':'东财 vs 开盘红跌停复盘','a':dt_count,'b':resume_dt,'diff':abs(dt_count-resume_dt),'pass':abs(dt_count-resume_dt)<=2})
    verified=bool(em) and (not checks or sum(1 for c in checks if c['pass']) >= max(1, math.ceil(len(checks)*0.67)))

    # Current ladder / highest board.
    max_board=max(int(x.get('continuous') or 1) for x in current)
    highest=[x for x in current if int(x.get('continuous') or 1)==max_board]
    zttt_rows=[]
    if isinstance(zttt_obj,dict):
        for z in zttt_obj.get('StockList') or []:
            try:
                if isinstance(z,(list,tuple)) and len(z)>5:
                    zttt_rows.append({'code':str(z[0]).zfill(6),'name':str(z[1]),'board':int(_num(z[2],1)),'theme':str(z[5] or '')})
            except Exception:
                continue
    if zttt_rows:
        zmax=max(z['board'] for z in zttt_rows)
        zhigh={z['code'] for z in zttt_rows if z['board']==zmax}
        ehigh={x['code'] for x in highest}
        ladder_pass=(zmax==max_board and (not zhigh or not ehigh or bool(zhigh & ehigh)))
        checks.append({'name':'东财连板池 vs 开盘红天梯','a':max_board,'b':zmax,'diff':abs(max_board-zmax),'pass':ladder_pass})
        if not ladder_pass:
            verified=False
    ladder=[]
    by_board=defaultdict(list)
    for x in current:
        by_board[int(x.get('continuous') or 1)].append(x)
    for b in sorted(by_board,reverse=True):
        arr=sorted(by_board[b],key=lambda z:(_time_key(z.get('first_zt_time')),_num(z.get('amount'))))
        ladder.append({'board':b,'count':len(arr),'stocks':[{'code':z['code'],'name':z['name'],'theme':(_theme_key(z) or ['其他'])[0],'sector':z.get('sector',''),'reason':z.get('reason',''),'first_zt_time':z.get('first_zt_time','')} for z in arr]})

    # Find 3 recent trading dates and historical pools.
    dates=_find_trade_dates(lk,3)
    today=datetime.now(CN_TZ).date().isoformat()
    if today not in dates and current:
        dates=(dates+[today])[-3:]
    historical={}
    for d in dates:
        if d==today:
            historical[d]=current
            continue
        # KPH has richer historical reasons/themes; EM is fallback.
        obj,err=_safe_call(lk,'limit_up_his_kph',date=d)
        arr=_normalize_kph(_rows(obj))
        if not arr:
            obj2,err2=_safe_call(lk,'stock_zt_pool_em',date=d.replace('-',''))
            arr=_normalize_em_zt(_rows(obj2))
        historical[d]=arr

    day_blocks=[]
    for d in dates:
        arr=historical.get(d) or []
        day_blocks.append({'date':d,'themes':_theme_aggregate(arr,d)[:12],'zt_count':len(arr)})
    rotation=_rotation_compare(day_blocks)

    # CLS board rotation is kept as a second independent view when available.
    cls_rotation=_cls_rotation(lk)

    # Previous day for promotion rates.
    prev=[]
    if len(dates)>=2:
        prev=historical.get(dates[-2]) or []
    promotion=_promotion_rates(prev,current)

    # Yesterday premium from EastMoney if available.
    yz_obj,yz_err=_safe_call(lk,'stock_yesterday_zt_em')
    yz=_rows(yz_obj)
    premiums=[_num(r.get('change_pct') or r.get('涨跌幅')) for r in yz if r.get('change_pct') is not None or r.get('涨跌幅') is not None]
    yesterday_premium=round(sum(premiums)/len(premiums),2) if premiums else None

    # Role model is descriptive, based on current limit pool structure.
    roles=_roles(current)

    # Dedicated theme summary for top current themes.
    today_themes=day_blocks[-1]['themes'] if day_blocks else []

    return {
        'ok':True,'verified':verified,'trade_date':today,'updated_at':datetime.now(CN_TZ).isoformat(timespec='seconds'),
        'zt_count':zt_count,'dt_count':dt_count,'zb_count':(int(_num(emo.get('up_open_num'))) if emo.get('up_open_num') is not None else None),
        'seal_rate':(_num(emo.get('up_ratio')) if emo.get('up_ratio') is not None else None),'max_board':max_board,
        'highest_stocks':[{'code':x['code'],'name':x['name'],'board':x['continuous'],'theme':(_theme_key(x) or ['其他'])[0],'sector':x.get('sector',''),'reason':x.get('reason','')} for x in highest],
        'ladder':ladder,'promotion_rates':promotion,'yesterday_premium':yesterday_premium,
        'roles':roles,'theme_today':today_themes,'rotation3':rotation,'sector_rotation3':cls_rotation,
        'checks':checks,'source_status':source_status,
        'reference_counts':{'eastmoney':{'zt':zt_count,'dt':dt_count},'kph_emotion':{'zt':kph_zt,'dt':kph_dt},'kph_resumption':{'zt':resume_zt,'dt':resume_dt}},
        'note':'涨停/跌停/连板采用专门涨跌停池与开盘红涨停复盘/天梯交叉校准，不再从全A涨幅近似推算；源冲突时关键数字不发布。题材轮动固定比较今天+前2个交易日。',
        'elapsed_ms':int((time.time()-started)*1000),
    }
