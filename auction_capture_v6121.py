from __future__ import annotations
import json, math, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from public_sources import fetch_eastmoney_all_a, fetch_history_df, EASTMONEY_SPOT_HOSTS, EASTMONEY_HEADERS

CN=ZoneInfo('Asia/Shanghai')
_STATUS={'state':'idle','trade_date':None,'mode':None,'rows':0,'total':0,'updated_at':None,'error':None,'history_days':0,'daily_close_rows':0}
_LOCK=threading.RLock()
_RUNNING=False


def _now(): return datetime.now(CN)
def _f(v,d=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception:return d

def _full_code(code:str)->str:
    c=str(code).zfill(6)
    if c.startswith(('6','9')): return 'sh'+c
    if c.startswith(('0','3')): return 'sz'+c
    if c.startswith(('4','8')): return 'bj'+c
    return c

def _status(**kw):
    with _LOCK:
        _STATUS.update(kw,updated_at=_now().isoformat(timespec='seconds'))
        return dict(_STATUS)

def get_status():
    with _LOCK:return dict(_STATUS)

def count_day(db:Path,day:str)->int:
    try:
        with sqlite3.connect(db,timeout=8) as c:
            return int(c.execute('SELECT COUNT(*) FROM auction25 WHERE trade_date=?',(day,)).fetchone()[0])
    except Exception:return 0

def _upsert_rows(db:Path, rows:list[tuple]):
    if not rows:return 0
    with sqlite3.connect(db,timeout=20) as c:
        c.executemany('''INSERT OR REPLACE INTO auction25
          (trade_date,code,captured_at,source,source_timestamp,price,amount,volume,prev_close,float_cap,yesterday_amount,data)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
    return len(rows)

def capture_eastmoney_925(db:Path, allow_window=True):
    """Live primary capture. Called repeatedly around 09:25; only provider rows timestamped 09:25 are accepted."""
    import requests
    dt=_now()
    if dt.weekday()>4:return {'ok':False,'reason':'非交易日'}
    if allow_window:
        sec=dt.hour*3600+dt.minute*60+dt.second
        if not (9*3600+24*60+45 <= sec <= 9*3600+27*60+30):
            return {'ok':False,'reason':'实时主采集窗口为09:24:45-09:27:30'}
    params={'pn':1,'pz':8000,'po':1,'np':1,'ut':'bd1d9ddb04089700cf9c27f6f7426281','fltt':2,'invt':2,'fid':'f3',
      'fs':'m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048','fields':'f2,f3,f5,f6,f8,f12,f14,f18,f21,f124'}
    errs=[];raw=[];provider=''
    for url in EASTMONEY_SPOT_HOSTS:
        try:
            j=requests.get(url,params=params,headers=EASTMONEY_HEADERS,timeout=3.2).json(); d=j.get('data') or {};raw=d.get('diff') or []
            if isinstance(raw,dict):raw=list(raw.values())
            exp=int(d.get('total') or len(raw) or 0)
            if len(raw)<min(4300,max(1,int(exp*.88))):raise ValueError(f'coverage {len(raw)}/{exp}')
            provider=url;break
        except Exception as e:errs.append(f'{url}:{type(e).__name__}')
    if not provider:return {'ok':False,'error':'东财09:25全市场快照不可用','source_errors':errs}
    rows=[];day=dt.date().isoformat();cap_ts=dt.isoformat(timespec='seconds')
    for x in raw:
        try:
            ts=datetime.fromtimestamp(int(x.get('f124')),CN)
            if ts.date()!=dt.date() or (ts.hour,ts.minute)!=(9,25):continue
            code=str(x.get('f12') or '').zfill(6);price=_f(x.get('f2'));amount=_f(x.get('f6'))
            if len(code)!=6 or price<=0 or amount<0:continue
            rows.append((day,code,cap_ts,'东方财富09:25最终快照',ts.isoformat(timespec='seconds'),price,amount,_f(x.get('f5')),
                         _f(x.get('f18')), _f(x.get('f21')),None,json.dumps(x,ensure_ascii=False)))
        except Exception:continue
    n=_upsert_rows(db,rows)
    return {'ok':bool(n),'rows':n,'total':len(raw),'source':'东方财富09:25最终快照','trade_date':day,'error':None if n else '没有返回带09:25源时间的有效记录'}

def _to_jsonable(x):
    try:
        from eltdx import to_jsonable
        return to_jsonable(x)
    except Exception:
        if hasattr(x,'model_dump'): return x.model_dump()
        if hasattr(x,'__dict__'): return dict(x.__dict__)
        return x

def _flatten_openings(obj):
    obj=_to_jsonable(obj)
    out=[]
    if obj is None:return out
    if isinstance(obj,list):
        for x in obj:out.extend(_flatten_openings(x))
    elif isinstance(obj,dict):
        # one TradeTick or keyed/batched result
        keys=set(obj.keys())
        if {'price','volume'} & keys and ('code' in keys or 'full_code' in keys):out.append(obj)
        else:
            for v in obj.values():out.extend(_flatten_openings(v))
    return out

def _fetch_universe():
    df,meta=fetch_eastmoney_all_a()
    if df is None or df.empty:raise RuntimeError('全A代码表为空')
    rows={}
    for x in df.to_dict('records'):
        c=str(x.get('code') or '').zfill(6)
        if len(c)!=6:continue
        rows[c]={'code':c,'full':_full_code(c),'prev_close':_f(x.get('settlement')),'float_cap':_f(x.get('nmc'))*10000,'name':x.get('name')}
    if len(rows)<3000:raise RuntimeError(f'全A代码表覆盖不足:{len(rows)}')
    return rows,meta

def _eltdx_opening_batch(full_codes:list[str], day:str):
    from eltdx import TdxClient
    with TdxClient(timeout=4) as client:
        # official API supports list input; batch_size keeps requests bounded
        data=client.trades.opening_match_history(full_codes,day,batch_size=160)
    return _flatten_openings(data)

def _historical_days(day:str, count:int=6):
    d=datetime.fromisoformat(day).date();out=[]
    # Use weekdays as candidates; eltdx returns no opening record for non-trading dates.
    for i in range(0,16):
        x=d-timedelta(days=i)
        if x.weekday()<5:out.append(x.isoformat())
        if len(out)>=count:break
    return out

def _store_eltdx_day(db:Path, day:str, universe:dict, is_target:bool):
    full=[v['full'] for v in universe.values() if v['full'].startswith(('sh','sz','bj'))]
    openings=[]
    # chunk at application level as well; if one batch fails, others survive.
    for i in range(0,len(full),800):
        try:openings.extend(_eltdx_opening_batch(full[i:i+800],day))
        except Exception:continue
    nowts=_now().isoformat(timespec='seconds');rows=[]
    for x in openings:
        code=str(x.get('code') or x.get('full_code') or '').lower().replace('sh','').replace('sz','').replace('bj','')[-6:]
        if code not in universe:continue
        price=_f(x.get('price'));vol=_f(x.get('volume'));amt=_f(x.get('trade_amount_yuan') or x.get('amount'))
        if amt<=0 and price>0 and vol>0:amt=price*vol*100
        if price<=0 or amt<=0:continue
        u=universe[code]
        prev=u['prev_close'] if is_target else 0.0
        cap=u['float_cap'] if is_target else 0.0
        st=str(x.get('time_label') or '09:25:00')
        rows.append((day,code,nowts,'eltdx历史09:25正式撮合',f'{day}T{st}+08:00',price,amt,vol,prev,cap,None,json.dumps(x,ensure_ascii=False,default=str)))
    return _upsert_rows(db,rows)

def _fill_prev_close_amounts(db:Path,target_day:str, prev_day:str, limit=420):
    """Only enrich plausible current candidates, avoiding thousands of historical HTTP calls."""
    with sqlite3.connect(db,timeout=10) as c:
        c.row_factory=sqlite3.Row
        rows=[dict(x) for x in c.execute('SELECT code,amount,price,prev_close,float_cap FROM auction25 WHERE trade_date=? ORDER BY amount DESC LIMIT ?',(target_day,limit)).fetchall()]
    def one(r):
        try:
            df,src=fetch_history_df(r['code'],30,use_cache=True)
            if df is None or df.empty:return None
            hit=df[df['date'].astype(str)==prev_day]
            if hit.empty:return None
            a=_f(hit.iloc[-1].get('amount'))
            return (prev_day,r['code'],a,_f(hit.iloc[-1].get('close')),str(prev_day)+'T15:00:00+08:00') if a>0 else None
        except Exception:return None
    vals=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs=[ex.submit(one,r) for r in rows]
        for f in as_completed(futs):
            v=f.result()
            if v:vals.append(v)
    if vals:
        with sqlite3.connect(db,timeout=15) as c:
            c.executemany('INSERT OR REPLACE INTO daily_close(trade_date,code,amount,price,source_timestamp) VALUES(?,?,?,?,?)',vals)
    return len(vals)

def backfill(db:Path, trade_date:str|None=None, history_days:int=6):
    global _RUNNING
    with _LOCK:
        if _RUNNING:return {'ok':False,'state':'running','status':dict(_STATUS)}
        _RUNNING=True
    day=trade_date or _now().date().isoformat()
    try:
        _status(state='running',trade_date=day,mode='eltdx-history-backfill',error=None,rows=0,history_days=0,daily_close_rows=0)
        universe,meta=_fetch_universe(); days=_historical_days(day,max(2,min(8,history_days)))
        done=[];totalrows=0
        for d in days:
            n=_store_eltdx_day(db,d,universe,is_target=(d==day))
            if n:done.append(d);totalrows+=n
            _status(rows=count_day(db,day),total=totalrows,history_days=len(done))
        # Identify actual prior archive date, then add previous full-day turnover amounts for top candidates.
        prev=None
        with sqlite3.connect(db,timeout=10) as c:
            r=c.execute('SELECT MAX(trade_date) FROM auction25 WHERE trade_date<?',(day,)).fetchone();prev=r[0] if r else None
        close_rows=_fill_prev_close_amounts(db,day,prev) if prev else 0
        final=count_day(db,day)
        return _status(state='complete' if final else 'error',rows=final,total=totalrows,history_days=len(done),daily_close_rows=close_rows,
                       error=None if final else 'eltdx未返回目标日期09:25正式撮合记录') | {'ok':bool(final),'trade_date':day}
    except Exception as e:
        return _status(state='error',trade_date=day,error=f'{type(e).__name__}: {e}') | {'ok':False}
    finally:
        with _LOCK:_RUNNING=False

def start_backfill(db:Path,trade_date:str|None=None,history_days:int=6):
    global _RUNNING
    with _LOCK:
        if _RUNNING:return get_status()
    threading.Thread(target=backfill,args=(db,trade_date,history_days),daemon=True,name='auction-eltdx-backfill').start()
    time.sleep(.03)
    return get_status()
