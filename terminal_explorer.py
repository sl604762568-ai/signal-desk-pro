"""Independent public-data sector explorer and *observed-universe* minute scanner.
No demonstration prices or fabricated memberships.  All minute comparisons are made
from timestamps captured by this server and are unavailable until enough samples exist.
"""
from __future__ import annotations
import math
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List

from sector_engine import fetch_board_list, fetch_board_members
from public_sources import fetch_tencent_quotes, fetch_history_df

TZ = ZoneInfo('Asia/Shanghai')
REGIONS = ('北京','上海','广东','深圳','江苏','浙江','山东','安徽','福建','河南','河北','湖北','湖南','四川','重庆','陕西','辽宁','吉林','黑龙江','江西','广西','云南','贵州','海南','山西','甘肃','新疆','宁夏','内蒙古','青海','西藏','天津')
STYLE_TERMS = ('央企','国企','红利','高股息','低价','破净','小盘','大盘','高送转','融资融券','沪股通','深股通','MSCI','次新','绩优','价值')
HOT_GROUPS = {
 '科技': ('人工智能','AI','算力','软件','数据中心','通信','机器人','云计算','网络安全'),
 '芯片': ('半导体','集成电路','芯片','光刻','封测','先进封装','晶圆'),
 '存储': ('存储','DRAM','闪存','HBM','存储芯片'),
 '电力': ('电力','电网','储能','特高压','虚拟电厂','核电','风电','光伏'),
 '机器人': ('机器人','减速器','工业母机','自动化','伺服'),
 '新能源': ('固态电池','锂电','新能源汽车','充电桩','氢能'),
 '消费': ('消费电子','家电','食品','零售','白酒','旅游'),
}
_pools: Dict[str, Dict[str, Any]] = {}
_sector_lock = threading.RLock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='sector-explorer')
_quote_lock = threading.RLock()
_observed: Dict[str, deque] = {}
_observed_day = ''
_last_quote_batch = {'ts':0.0,'rows':[],'error':None,'symbols':0}
_ipo_cache: Dict[str, tuple[float,int|None]] = {}
_MAX_CODES = 160


def _f(v: Any) -> float | None:
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _fetch_category(category: str, group: str) -> Dict[str, Any]:
    if category == 'industry':
        boards = fetch_board_list('industry', 500)
    elif category == 'concept':
        boards = fetch_board_list('concept', 550)
    elif category in ('hot', 'style'):
        boards = fetch_board_list('concept', 550)
        if category == 'style':
            boards = [b for b in boards if any(x.upper() in b['name'].upper() for x in STYLE_TERMS)]
        elif group and group in HOT_GROUPS:
            boards = [b for b in boards if any(x.upper() in b['name'].upper() for x in HOT_GROUPS[group])]
        elif group:
            boards = []
        else:
            boards = [b for b in boards if any(x.upper() in b['name'].upper() for words in HOT_GROUPS.values() for x in words)]
    elif category == 'region':
        # Region taxonomy is NOT interchangeable with an industry/concept name.
        # The board's own region list is probed using a separate Eastmoney fs rule.
        # Do not monkey-patch global _board_fs (could affect simultaneous tasks).
        from sector_engine import _clist_json
        params={'pz':100,'po':1,'np':1,'pn':1,'fid':'f3','fs':'m:90+t:1+f:!50',
                'ut':'bd1d9ddb04089700cf9c27f6f7426281','fltt':2,'invt':2,
                'fields':'f2,f3,f6,f8,f12,f14,f62,f164,f174,f109,f160,f104,f105,f106,f184,f204,f205'}
        obj=_clist_json(params)
        raw=list(((obj or {}).get('data') or {}).get('diff') or [])
        boards=[]
        for b in raw:
            name=str(b.get('f14') or '')
            if not any(n in name for n in REGIONS):
                continue
            up,down,flat=(_f(b.get(k)) or 0 for k in ('f104','f105','f106'))
            boards.append({'code':str(b.get('f12') or ''),'name':name,'type':'region',
                'pct':_f(b.get('f3')),'amount':_f(b.get('f6')),
                'main_net':_f(b.get('f62')),'main_net_pct':_f(b.get('f184')),
                'main_net_5d':_f(b.get('f164')),'main_net_10d':_f(b.get('f174')),
                'pct_5d':_f(b.get('f109')),'pct_10d':_f(b.get('f160')),
                'turnover':_f(b.get('f8')),'up':int(up),'down':int(down),'flat':int(flat),
                'breadth':round(up/(up+down+flat)*100,2) if up+down+flat else None,
                'leader_name':str(b.get('f204') or ''),'leader_code':str(b.get('f205') or '')})
        if not boards:
            raise RuntimeError('地域分类接口未返回可核验省市板块')
    else:
        raise ValueError('unknown sector category')
    valid=[b for b in boards if str(b.get('code','')).startswith('BK') and b.get('name')]
    # Comparable within this returned board population, not an outcome forecast.
    for b in valid:
        pct=b.get('pct'); breadth=b.get('breadth'); flow=b.get('main_net_pct')
        if pct is None or breadth is None or flow is None:
            b['heat_score']=None
            b['flow_note']='资金或广度字段缺失，不进行热度量化'
        else:
            b['heat_score']=round(0.4*sum(1 for z in valid if z.get('pct') is not None and z['pct']<=pct)/len(valid)*100
                  +0.3*sum(1 for z in valid if z.get('breadth') is not None and z['breadth']<=breadth)/len(valid)*100
                  +0.3*sum(1 for z in valid if z.get('main_net_pct') is not None and z['main_net_pct']<=flow)/len(valid)*100,1)
            b['flow_note']=('价格与资金同向' if pct*flow>0 else '价格与资金方向背离' if pct*flow<0 else '资金或价格方向未明')
    valid.sort(key=lambda x:(x.get('pct') is not None, x.get('pct') or -999),reverse=True)
    return {'category':category,'group':group,'source':'东方财富真实板块列表',
            'classification':'真实行业/概念/地域成分板块' if category in ('industry','concept','region') else '真实概念板块按名称分组，不推断股票归属',
            'updated_at':datetime.now(TZ).isoformat(timespec='seconds'),'total':len(valid),'boards':valid}


def _complete(key: str, f) -> None:
    try:
        result=f.result()
        with _sector_lock:
            _pools[key]={'ts':time.time(),'data':result,'future':None,'error':None}
    except Exception as ex:
        with _sector_lock:
            past=_pools.get(key,{})
            _pools[key]={'ts':time.time(),'data':past.get('data'),'future':None,'error':f'{type(ex).__name__}: {ex}'}


def explore(category='industry',group='',limit=100):
    if category not in ('industry','concept','hot','style','region'):
        raise ValueError('unsupported category')
    group=group.strip()[:30]
    key=f'{category}:{group}'
    with _sector_lock:
        obj=_pools.get(key,{})
        data=obj.get('data')
        age=time.time()-obj.get('ts',0)
        if data and age<180:
            return {**data,'boards':data['boards'][:limit],'cached':True}
        if obj.get('future') is None and (age>30 or not obj.get('error')):
            fut=_pool.submit(_fetch_category,category,group)
            _pools[key]={'ts':time.time(),'data':data,'future':fut,'error':None}
            fut.add_done_callback(lambda f,k=key:_complete(k,f))
        if data:
            return {**data,'boards':data['boards'][:limit],'stale':True,'refreshing':True}
        err=obj.get('error') if age<30 else None
        return {'category':category,'group':group,'boards':[],'loading':not bool(err),
                'error':err,'updated_at':None,'source':'东方财富','total':0}


def sector_members(board_code:str,limit=100):
    if not re.fullmatch(r'BK\d{4,8}',board_code.upper()):
        raise ValueError('invalid board code')
    rows=fetch_board_members(board_code.upper(),limit=min(limit,400))
    return {'board_code':board_code,'members':rows,'count':len(rows),
            'source':'东方财富真实板块成分','updated_at':datetime.now(TZ).isoformat(timespec='seconds')}


def _stamp(row):
    return {'price':_f(row.get('price')),'amount':_f(row.get('amount')),
            'pct':_f(row.get('pct')),'turnover_rate':_f(row.get('turnover_rate')),
            'volume_ratio':_f(row.get('volume_ratio')),'name':row.get('name','')}


def verified_listing_age(code:str)->int|None:
    item=_ipo_cache.get(code)
    if item and time.time()-item[0]<86400:return item[1]
    import requests
    from datetime import date
    market='1' if code.startswith('6') else '0'
    try:
        rsp=requests.get('https://push2.eastmoney.com/api/qt/stock/get',
            params={'secid':f'{market}.{code}','fields':'f26','ut':'fa5fd1943c7b386f172d6893dbfba10b'},
            headers={'User-Agent':'Mozilla/5.0'},timeout=2.5)
        rsp.raise_for_status()
        raw=str(((rsp.json() or {}).get('data') or {}).get('f26') or '')
        if re.fullmatch(r'\d{8}',raw):
            listed=date(int(raw[:4]),int(raw[4:6]),int(raw[6:]))
            age=(datetime.now(TZ).date()-listed).days
        else:age=None
    except Exception:age=None
    _ipo_cache[code]=(time.time(),age)
    return age


def _trade_session()->bool:
    dt=datetime.now(TZ)
    hm=dt.hour*60+dt.minute
    return dt.weekday()<5 and (570<=hm<690 or 780<=hm<900)


def observe(codes:List[str]):
    """Capture actual Tencent quote snapshots for at most 160 selected names.
    Does not claim to monitor the whole A-share market on a sleeping free server.
    """
    global _observed_day
    clean=list(dict.fromkeys(str(s).zfill(6) for s in codes if re.fullmatch(r'\d{1,6}',str(s))))[:_MAX_CODES]
    now=time.time()
    day=datetime.now(TZ).strftime('%Y-%m-%d')
    with _quote_lock:
        if _observed_day != day:
            _observed.clear();_observed_day=day
        if now-_last_quote_batch['ts']<40 and set(clean).issubset(set(x['code'] for x in _last_quote_batch['rows'])):
            return {**_last_quote_batch,'cached':True}
    if not clean:
        return {'rows':[],'error':'无监控股票，请先进入真实行情池','symbols':0,'ts':now}
    rows,meta=fetch_tencent_quotes(clean)
    with _quote_lock:
        if rows:
            for r in rows:
                # Tencent f37 is conventionally quoted in 10k yuan; normalize to yuan.
                if r.get('amount') is not None:r['amount']=r['amount']*10000
                code=r['code']
                hist=_observed.setdefault(code,deque(maxlen=22))
                fresh=bool(r.get('quote_time'))
                if r.get('quote_time'):
                    try:
                        raw=str(r['quote_time'])[:14]
                        sample_time=datetime.strptime(raw,'%Y%m%d%H%M%S').replace(tzinfo=TZ).timestamp()
                        fresh=abs(now-sample_time)<=150
                    except Exception:fresh=False
                if fresh and _trade_session() and (not hist or now-hist[-1][0]>=35):
                    hist.append((now,_stamp(r)))
        result={'ts':now,'rows':rows,'symbols':len(clean),'provider':'腾讯财经批量行情',
                'error':'; '.join(meta.get('errors') or []) if not rows else None,
                'coverage':len(rows)/len(clean) if clean else 0,
                'updated_at':datetime.now(TZ).isoformat(timespec='seconds')}
        if rows:_last_quote_batch.update(result)
    return result


def _rate(a,b):
    return (a/b-1)*100 if a is not None and b and b>0 else None


def _signals(code:str,latest:dict):
    h=list(_observed.get(code,[]))
    if not h or not _trade_session() or time.time()-h[-1][0]>180:return {'speed_1m_pct':None,'amplitude_10m_pct':None,'volume_increasing_3':None,'observations':len(h)}
    now,cur=h[-1]
    one=next(((t,r) for t,r in reversed(h[:-1]) if 45<=now-t<=155),None)
    ten=next(((t,r) for t,r in reversed(h[:-1]) if 535<=now-t<=740),None)
    speed=_rate(cur['price'],one[1]['price']) if one else None
    amp=None
    if ten:
        window=[r['price'] for t,r in h if t>=ten[0] and r['price'] is not None]
        if len(window)>=7 and min(window)>0:
            amp=(max(window)-min(window))/min(window)*100
    rising=None
    # Three consecutive *completed* interval notional amounts, not cumulative turnover.
    if len(h)>=4 and all(40<=h[j][0]-h[j-1][0]<=160 for j in (-3,-2,-1)):
        diffs=[h[i][1]['amount']-h[i-1][1]['amount'] for i in (-3,-2,-1) \
               if h[i][1]['amount'] is not None and h[i-1][1]['amount'] is not None]
        if len(diffs)==3 and min(diffs)>=0:
            rising=diffs[2]>diffs[1]>diffs[0]
    return {'speed_1m_pct':round(speed,3) if speed is not None else None,
            'amplitude_10m_pct':round(amp,3) if amp is not None else None,
            'volume_increasing_3':rising,'observations':len(h),'observation_span_seconds':round(now-h[0][0]),
            'minute_source':'服务器真实逐分钟采集快照（仅监控池，需真实行情时间戳）'}


def scan(codes:List[str],conditions:Dict[str,Any]|None=None,sort='speed_1m_pct',limit=50,metadata:Dict[str,dict]|None=None):
    conditions=conditions or {}
    metadata=metadata or {}
    obs=observe(codes)
    with _quote_lock:
        rows=[{**r,**{k:v for k,v in (metadata.get(r['code']) or {}).items() if k in ('float_market_cap','industry')},**_signals(r['code'],r)} for r in obs.get('rows',[])]
    if conditions.get('not_new_5pct'):
        for r in rows[:20]:r['verified_ipo_age_days']=verified_listing_age(r['code'])
    # MA5/MA10 only for a narrow set and only when requested.  A failed K-line
    # request never gets interpreted as a false/pass signal.
    use_ma='ma_relation' in conditions and conditions.get('ma_relation') not in ('','any',None)
    if use_ma:
        for r in rows[:min(len(rows),20)]:
            try:
                df,src=fetch_history_df(r['code'],count=15)
                closes=df['close'].dropna().astype(float).tolist()
                if len(closes)>=10:
                    r['ma5']=round(sum(closes[-5:])/5,3)
                    r['ma10']=round(sum(closes[-10:])/10,3)
                    r['ma_source']=src
            except Exception:r['ma5']=r['ma10']=None
    def ok(r):
        for key,val in conditions.items():
            if val is None or val=='':continue
            if key=='ma_relation':
                if val=='any':continue
                if r.get('ma5') is None or r.get('ma10') is None:return False
                if val=='gt' and not r['ma5']>r['ma10']:return False
                if val=='lt' and not r['ma5']<r['ma10']:return False
            elif key=='rising_volume':
                if val and r.get('volume_increasing_3') is not True:return False
            elif key=='not_new_5pct':
                # We cannot infer IPO age from ticker or name; absence of an IPO
                # date is unknown. Filter only via verified listing metadata when available.
                if val and not (r.get('pct',0)>5 and (r.get('verified_ipo_age_days') or 0)>=60):return False
            else:
                keys={'min_price':('price',lambda x,v:x>=v),'max_price':('price',lambda x,v:x<=v),
                    'min_pct':('pct',lambda x,v:x>=v),'max_pct':('pct',lambda x,v:x<=v),
                    'min_amount':('amount',lambda x,v:x>=v),'max_float_cap':('float_market_cap',lambda x,v:x<=v),
                    'min_float_cap':('float_market_cap',lambda x,v:x>=v),
                    'min_volume_ratio':('volume_ratio',lambda x,v:x>=v),
                    'min_turnover':('turnover_rate',lambda x,v:x>=v),
                    'min_speed':('speed_1m_pct',lambda x,v:x>=v),
                    'max_speed':('speed_1m_pct',lambda x,v:x<=v),
                    'min_amplitude_10m':('amplitude_10m_pct',lambda x,v:x>=v)}
                if key not in keys:continue
                field,fn=keys[key]
                x=_f(r.get(field));v=_f(val)
                if x is None or v is None or not fn(x,v):return False
        return True
    filtered=[r for r in rows if ok(r)]
    sort=sort if sort in ('speed_1m_pct','pct','amount','volume_ratio','turnover_rate','amplitude_10m_pct') else 'speed_1m_pct'
    filtered.sort(key=lambda r:(r.get(sort) is not None,r.get(sort) if r.get(sort) is not None else -1e50),reverse=True)
    return {'rows':filtered[:max(1,min(limit,160))],'matched':len(filtered),'observed':len(rows),
        'requested':len(codes),'source':obs.get('provider'),'updated_at':obs.get('updated_at'),
        'coverage':obs.get('coverage',0),'error':obs.get('error'),
        'note':'1分钟和10分钟仅基于本服务器在交易时段实际连续采集的监控池；缺历史快照时显示缺失，不用日线猜分钟指标。' 
    }
