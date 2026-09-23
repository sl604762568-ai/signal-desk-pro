"""v6.11: auditable persisted review/watchlist/auction/technical scan.
All externally sourced market values require provenance; no invented live data.
Inspired by data-source priority in https://github.com/simonlin1212/a-stock-data
(the upstream is a coding skill, not a pip-ready market-data service).
"""
from __future__ import annotations
import json, math, os, re, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import requests
from public_sources import fetch_history_df, fetch_tencent_quotes, fetch_eastmoney_all_a
from v611_history import fetch_strict_bars
CN=ZoneInfo('Asia/Shanghai')
DB=Path(os.getenv('DB_PATH',str(Path(__file__).with_name('sentiment.db'))))
LOCK=threading.RLock()
TECH={'status':'idle','scanned':0,'total':0,'hits':0,'updated_at':None,'error':None}

def now():return datetime.now(CN)
def f(v,default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError,ValueError):return default

def init():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(DB,timeout=10) as c:
        c.execute('PRAGMA busy_timeout=10000')
        c.execute('''CREATE TABLE IF NOT EXISTS frozen_picks(trade_date TEXT NOT NULL, kind TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(trade_date,kind))''')
        c.execute('''CREATE TABLE IF NOT EXISTS watchlist(code TEXT PRIMARY KEY, added_at TEXT NOT NULL, added_price REAL NOT NULL, source TEXT, trigger_date TEXT, trigger_conditions TEXT, analysis TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS auction25(trade_date TEXT NOT NULL,code TEXT NOT NULL, captured_at TEXT NOT NULL, source TEXT NOT NULL, source_timestamp TEXT NOT NULL, price REAL NOT NULL, amount REAL NOT NULL, volume REAL, prev_close REAL, float_cap REAL, yesterday_amount REAL, data TEXT NOT NULL, PRIMARY KEY(trade_date,code))''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_close(trade_date TEXT NOT NULL,code TEXT NOT NULL,amount REAL NOT NULL,price REAL,source_timestamp TEXT NOT NULL,PRIMARY KEY(trade_date,code))''')
        c.execute('''CREATE TABLE IF NOT EXISTS technical_hits(trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, payload TEXT NOT NULL, PRIMARY KEY(trade_date,code))''')

def freeze_get(date,kind):
    with sqlite3.connect(DB,timeout=10) as c:
        x=c.execute('SELECT payload,captured_at FROM frozen_picks WHERE trade_date=? AND kind=?',(date,kind)).fetchone()
    return ({**json.loads(x[0]),'frozen':True,'frozen_at':x[1]} if x else None)

def freeze_latest(kind,on_or_before=None):
    """Read last actually persisted after-close pick; never re-run selection on a page refresh."""
    with sqlite3.connect(DB,timeout=10) as c:
        c.row_factory=sqlite3.Row
        if on_or_before:
            row=c.execute('SELECT * FROM frozen_picks WHERE kind=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1',(kind,on_or_before)).fetchone()
        else:
            row=c.execute('SELECT * FROM frozen_picks WHERE kind=? ORDER BY trade_date DESC LIMIT 1',(kind,)).fetchone()
    return {**json.loads(row['payload']),'frozen':True,'frozen_at':row['captured_at'],
      'trade_date':row['trade_date'],'latest_saved':True} if row else None

def freeze_put(date,kind,payload,force=False):
    if not payload.get('picks') and not payload.get('chan_picks') and int(payload.get('scanned') or 0)<=0:return False
    ts=now().isoformat(timespec='seconds')
    with sqlite3.connect(DB,timeout=10) as c:
        if force:c.execute('DELETE FROM frozen_picks WHERE trade_date=? AND kind=?',(date,kind))
        c.execute('INSERT OR IGNORE INTO frozen_picks VALUES(?,?,?,?)',(date,kind,ts,json.dumps(payload,ensure_ascii=False,default=str)))
    return True

def code_ok(code):return bool(re.fullmatch(r'\d{6}',str(code or '')))

def quote_by_code(code):
    if not code_ok(code):raise ValueError('请输入6位A股代码')
    rows,meta=fetch_tencent_quotes([code]);item=next((x for x in rows if x.get('code')==code),None)
    if not item or f(item.get('price'))<=0:raise RuntimeError('腾讯实时行情未返回有效价格')
    item['source']=meta.get('provider','腾讯财经');item['checked_at']=now().isoformat(timespec='seconds')
    return item

def watch_add(code,source='手动添加',trigger_date=None,trigger_conditions=None):
    q=quote_by_code(code);stamp=now().isoformat(timespec='seconds')
    with sqlite3.connect(DB,timeout=10) as c:
        c.execute('INSERT OR IGNORE INTO watchlist(code,added_at,added_price,source,trigger_date,trigger_conditions) VALUES(?,?,?,?,?,?)',
            (code,stamp,q['price'],source,trigger_date,json.dumps(trigger_conditions or [],ensure_ascii=False)))
    return watch_get(code)

def watch_get(code):
    with sqlite3.connect(DB,timeout=10) as c:
        c.row_factory=sqlite3.Row;x=c.execute('SELECT * FROM watchlist WHERE code=?',(code,)).fetchone()
    return dict(x) if x else None

def watch_list():
    with sqlite3.connect(DB,timeout=10) as c:
        c.row_factory=sqlite3.Row;rows=[dict(x) for x in c.execute('SELECT * FROM watchlist ORDER BY added_at DESC').fetchall()]
    if not rows:return {'items':[],'source':None,'updated_at':now().isoformat(timespec='seconds')}
    qq,meta=fetch_tencent_quotes([r['code'] for r in rows]);by={q['code']:q for q in qq}
    for r in rows:
        q=by.get(r['code']);r['trigger_conditions']=json.loads(r.get('trigger_conditions') or '[]')
        r['quote_ok']=bool(q)
        if not q:
            r.update(name=None,price=None,change_pct=None,since_added_pct=None,analysis='实时价格不可用，暂停评价')
            continue
        price=f(q.get('price'));r.update(name=q.get('name'),price=price,change_pct=q.get('pct'),
            since_added_pct=round((price/f(r['added_price'])-1)*100,2) if f(r['added_price']) else None,
            source_quote=meta.get('provider'),quote_time=now().isoformat(timespec='seconds'))
        r['analysis']=f"自选以来变动 {r['since_added_pct']:+.2f}%；今日变动 {f(q.get('pct')):+.2f}%。持续观察量价及所处板块，非买卖指令。"
        q_turn=f(q.get('turnover_rate'));q_ratio=f(q.get('volume_ratio'));q_pct=q.get('pct')
        if q_turn>0 and q_ratio>0 and q_pct is not None:
            s_turn=min(100,round(q_turn/10*100))
            s_ratio=min(100,round(q_ratio/2.5*100))
            s_move=max(0,round(100-abs(f(q_pct)-3)*16))
            r['score']=round(.35*s_turn+.35*s_ratio+.3*s_move,1)
            r['score_label']='实时换手/量比/涨幅的研究关注分，不是投资结论'
        else:
            r['score']=None;r['score_label']='当前行情未提供完整量比/换手，暂不评分'
    return {'items':rows,'source':meta.get('provider'),'updated_at':now().isoformat(timespec='seconds')}

def watch_remove(code):
    with sqlite3.connect(DB,timeout=10) as c:c.execute('DELETE FROM watchlist WHERE code=?',(code,))

def auction_capture():
    """Capture Eastmoney final 09:25 batch only when ALL quote source timestamps match 09:25.
    Avoid interpreting 09:26+ daily total amount as opening call auction.
    Only genuine archived snapshots allow yesterday/5day comparisons.
    """
    dt=now()
    if dt.weekday()>4 or not (dt.hour==9 and dt.minute==25):
        return {'ok':False,'reason':'采集窗口仅限交易日北京时间09:25，禁止把盘中金额冒充竞价金额'}
    # Pull raw provider fields including f124 (per-security source epoch timestamp).
    from public_sources import EASTMONEY_SPOT_HOSTS,EASTMONEY_HEADERS
    params={'pn':1,'pz':8000,'po':1,'np':1,'ut':'bd1d9ddb04089700cf9c27f6f7426281','fltt':2,'invt':2,'fid':'f3',
     'fs':'m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',
     'fields':'f2,f3,f5,f6,f8,f12,f14,f18,f21,f124'}
    errs=[];raw=[];provider=''
    for url in EASTMONEY_SPOT_HOSTS:
        try:
            j=requests.get(url,params=params,headers=EASTMONEY_HEADERS,timeout=3.8).json()
            d=j.get('data') or {}; raw=d.get('diff') or []
            if isinstance(raw,dict):raw=list(raw.values())
            if len(raw)<min(4500,int(d.get('total') or 5000)*.9):raise ValueError('不是完整全市场快照')
            provider=url;break
        except Exception as exc:errs.append(f'{url}: {type(exc).__name__}')
    if not provider:return {'ok':False,'error':'竞价全市场快照不可用','source_errors':errs}
    valid=[];date=dt.date().isoformat()
    for x in raw:
        try:
            source_ts=datetime.fromtimestamp(int(x.get('f124')),CN)
            if source_ts.date()!=dt.date() or (source_ts.hour,source_ts.minute)!=(9,25):continue
            code=str(x.get('f12') or '')
            if not code_ok(code):continue
            price=f(x.get('f2'));amount=f(x.get('f6'))
            if price<=0 or amount<0:continue
            valid.append((date,code,dt.isoformat(timespec='seconds'),provider,source_ts.isoformat(timespec='seconds'),
                          price,amount,f(x.get('f5')),f(x.get('f18')),f(x.get('f21')),None,json.dumps(x,ensure_ascii=False)))
        except (ValueError,OverflowError,TypeError):continue
    if not valid:return {'ok':False,'error':'未获得带09:25交易所时刻的有效竞价记录'}
    with sqlite3.connect(DB,timeout=15) as c:
        c.executemany('INSERT OR IGNORE INTO auction25 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',valid)
    return {'ok':True,'rows':len(valid),'total':len(raw),'source':provider,'trade_date':date}

def record_daily_close():
    """Archive real full-session amounts only when vendor quote time confirms 15:00+ same day."""
    t=now()
    if t.weekday()>4 or not (15<=t.hour<=16):
        return {'ok':False,'reason':'不是当日收盘采集窗口'}
    from public_sources import EASTMONEY_SPOT_HOSTS,EASTMONEY_HEADERS
    params={'pn':1,'pz':8000,'po':1,'np':1,'ut':'bd1d9ddb04089700cf9c27f6f7426281',
     'fltt':2,'invt':2,'fid':'f3',
     'fs':'m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',
     'fields':'f2,f6,f12,f124'}
    source=None;raw=[]
    for host in EASTMONEY_SPOT_HOSTS:
        try:
            j=requests.get(host,params=params,headers=EASTMONEY_HEADERS,timeout=4).json()
            d=j.get('data') or {};raw=d.get('diff') or []
            if isinstance(raw,dict):raw=list(raw.values())
            if len(raw)<min(4500,int(d.get('total') or 5000)*.9):continue
            source=host;break
        except Exception:continue
    if not source:return {'ok':False,'error':'收盘全市场快照不可用'}
    date=t.date().isoformat();good=[]
    for x in raw:
        try:
            q=datetime.fromtimestamp(int(x.get('f124')),CN)
            if q.date()!=t.date() or q.hour<15:continue
            code=str(x.get('f12') or '')
            if not code_ok(code):continue
            amount=f(x.get('f6'));price=f(x.get('f2'))
            if price>0 and amount>=0:good.append((date,code,amount,price,q.isoformat(timespec='seconds')))
        except (TypeError,ValueError,OverflowError):continue
    if len(good)<min(4000,len(raw)*.8):return {'ok':False,'error':'收盘时间戳有效覆盖不足，拒绝归档'}
    with sqlite3.connect(DB,timeout=15) as c:
        c.executemany('INSERT OR REPLACE INTO daily_close VALUES(?,?,?,?,?)',good)
    return {'ok':True,'rows':len(good),'date':date,'source':source}

def auction_view(params=None):
    p=params or {};dt=str(p.get('trade_date') or now().date().isoformat())
    with sqlite3.connect(DB,timeout=10) as c:
        c.row_factory=sqlite3.Row; today=[dict(x) for x in c.execute('SELECT * FROM auction25 WHERE trade_date=?',(dt,))]
        dates=[x[0] for x in c.execute('SELECT DISTINCT trade_date FROM auction25 WHERE trade_date<? ORDER BY trade_date DESC LIMIT 5',(dt,))]
        history={}
        last_close_date=c.execute('SELECT MAX(trade_date) FROM daily_close WHERE trade_date<?',(dt,)).fetchone()[0]
        close_totals={r[0]:r[1] for r in c.execute('SELECT code,amount FROM daily_close WHERE trade_date=?',(last_close_date,))} if last_close_date else {}
        tracked={r[0] for r in c.execute('SELECT code FROM watchlist')}
        if dates:
            qs=','.join('?'*len(dates))
            for x in c.execute(f'SELECT trade_date,code,amount FROM auction25 WHERE trade_date IN ({qs})',dates):history.setdefault(x['code'],{})[x['trade_date']]=x['amount']
    items=[]
    for row in today:
        prior=history.get(row['code'],{});yesterday=prior.get(dates[0]) if dates else None
        avg5=sum(prior.values())/5 if len(prior)>=5 else None
        amount=f(row['amount']);price=f(row['price']);pre=f(row['prev_close'])
        # Float-cap amount is RMB, price × estimated float shares; no guessed opening turnover if missing.
        turnover=(amount/f(row['float_cap'])*100 if f(row['float_cap'])>0 else None)
        boom=amount/yesterday if yesterday and yesterday>0 else None
        gap=(price/pre-1)*100 if pre>0 else None
        item={'code':row['code'],'in_watchlist':row['code'] in tracked,'price':price,'amount':amount,'yesterday_amount':yesterday,'avg5_amount':avg5,
            'boom':boom,'turnover':turnover,'gap':gap,'float_cap':row['float_cap'],
            'source_timestamp':row['source_timestamp'],'source':row['source'],
            'amount_vs_prev_day_pct':round(amount/close_totals[row['code']]*100,4) if close_totals.get(row['code']) else None} # independently archived previous session
        # Filter only if every requested mandatory metric genuinely available.
        filters={'max_price':(price,lambda x:x<=f(p['max_price'])) if p.get('max_price') else None,
            'min_gap':(gap,lambda x:x>=f(p['min_gap'])) if p.get('min_gap') else None,
            'max_gap':(gap,lambda x:x<=f(p['max_gap'])) if p.get('max_gap') else None,
            'min_amount':(amount,lambda x:x>=f(p['min_amount'])) if p.get('min_amount') else None,
            'min_boom':(boom,lambda x:x>=f(p['min_boom'])) if p.get('min_boom') else None,
            'min_turnover':(turnover,lambda x:x>=f(p['min_turnover'])) if p.get('min_turnover') else None,
            'min_prev_day_pct':(item['amount_vs_prev_day_pct'],lambda x:x>=f(p['min_prev_day_pct'])) if p.get('min_prev_day_pct') else None}
        cap=f(row['float_cap'])
        dynamic_floor=max(f(p.get('min_amount'),5000000), 10000000 if 5e9<=cap<2e10 else 20000000 if cap>=2e10 else 5000000)
        item['dynamic_amount_floor']=dynamic_floor
        item['passes_available_filters']=(amount>=dynamic_floor) and all(v is None or (v[0] is not None and v[1](v[0])) for v in filters.values())
        if p.get('watch_only') and not item['in_watchlist']:item['passes_available_filters']=False
        item['complete_for_default']=boom is not None and turnover is not None and gap is not None and item['amount_vs_prev_day_pct'] is not None
        items.append(item)
    sort=str(p.get('sort') or 'amount')
    if sort not in {'amount','boom','gap','turnover'}:sort='amount'
    items.sort(key=lambda x:f(x.get(sort),-1),reverse=True)
    matched=[x for x in items if x['passes_available_filters']]
    return {'trade_date':dt,'items':matched[:max(1,min(200,int(p.get('limit') or 80)))],
            'total':len(items),'matched_total':len(matched),'history_dates':dates,'latest_daily_close':last_close_date,'source_status':'已采集09:25快照' if items else '尚未采集真实09:25最终快照',
            'note':'昨竞价和五日均值只从已存独立09:25快照计算；缺少已归档的昨全天成交额则占比不发布；默认门槛根据真实流通市值提高至500/1000/2000万元。未采集的交易日不可回填为实时数据。'}

def technical_evaluate(code,name,df,float_cap):
    """ALL explicit user criteria must hold. Periods = 30/3 trading sessions."""
    if not (code.startswith('00') or code.startswith('60')) or code.startswith(('688','689')) or any(x in name.upper() for x in ('ST','退')):return None
    if df is None or len(df)<36:return None
    df=df.copy();fields=('open','close','high','low','amount','turnover_rate','volume')
    for field in fields:
        if field not in df.columns:return None
    import pandas as pd
    for field in fields:df[field]=pd.to_numeric(df[field],errors='coerce')
    prev=df['close'].shift(1);day=df.tail(30);prev30=prev.tail(30)
    lim=prev30*1.10
    hit_zt=((day.close>=lim-.011)&(day.high>=lim-.011)).fillna(False)
    hit_bomb=((day.high>=lim-.011)&(day.close<lim-.011)).fillna(False)
    # Upper shadow as percentage of prior close, explicit reproducible definition.
    upper=100*(day.high-day[['open','close']].max(axis=1))/prev30
    candle=bool((upper>4.99).any());big=bool((day.amount>5e9).any())
    ma5=df.close.rolling(5).mean().iloc[-1];q3=df.turnover_rate.tail(3)
    last=df.iloc[-1]
    checks={'limit_up_30d':bool(hit_zt.any()),'failed_limit_30d':bool(hit_bomb.any()),'upper_shadow_30d':candle,
        'turnover_50yi_30d':big,'float_cap_gt_2_499yi':float_cap>249900000,
        'high_above_ma5':f(last.high)>f(ma5),'price_le_80':0<f(last.close)<=80,
        'turnover_gt_9_99_in_3d':bool((q3>9.99).any())}
    if not all(checks.values()):return None
    return {'code':code,'name':name,'price':round(f(last.close),2),'float_cap':float_cap,
         'checks':checks,'limit_up_count_30d':int(hit_zt.sum()),'failed_limit_count_30d':int(hit_bomb.sum()),
         'largest_upper_shadow_pct_30d':round(f(upper.max()),2),
         'max_amount_30d':round(f(day.amount.max()),2),'max_turnover_3d':round(f(q3.max()),2),
         'ma5':round(f(ma5),3),'history_end':str(last.get('date','')),'signal':'八项条件全部通过（严格AND）'}

def _scan_run(stocks,day):
    global TECH
    try:
        # Only persisted real historical bars; fetch failures are not interpreted as non-matches.
        def one(s):
            code=str(s.get('code') or '');name=str(s.get('name') or '');float_cap=f(s.get('float_market_cap'))
            if not code_ok(code) or not (code.startswith('00') or code.startswith('60')) or float_cap<=249900000:return None,None
            df,source=fetch_strict_bars(code,45)
            return technical_evaluate(code,name,df,float_cap),source
        with ThreadPoolExecutor(max_workers=3) as ex:
            jobs={ex.submit(one,s):s for s in stocks}
            for job in as_completed(jobs):
                try:
                    hit,src=job.result()
                    if hit:
                        hit['history_source']=src
                        with sqlite3.connect(DB,timeout=10) as c:c.execute('INSERT OR REPLACE INTO technical_hits VALUES(?,?,?,?)',(day,hit['code'],hit['name'],json.dumps(hit,ensure_ascii=False)))
                        TECH['hits']+=1
                except Exception as exc:TECH['errors']=TECH.get('errors',0)+1
                TECH['scanned']+=1
        TECH['status']='complete' if not TECH.get('errors') else 'partial'
    except Exception as exc:TECH.update(status='error',error=f'{type(exc).__name__}: {exc}')
    TECH['updated_at']=now().isoformat(timespec='seconds')

def tech_start(stocks,trade_date):
    with LOCK:
        if TECH['status']=='running':return dict(TECH)
        with sqlite3.connect(DB,timeout=10) as c:c.execute('DELETE FROM technical_hits WHERE trade_date=?',(trade_date,))
        TECH.update(day=trade_date,status='running',scanned=0,total=len(stocks),hits=0,errors=0,error=None,updated_at=now().isoformat(timespec='seconds'))
        threading.Thread(target=_scan_run,args=(stocks,trade_date),daemon=True,name='technical-full-scan').start()
        return dict(TECH)

def tech_results(date):
    with sqlite3.connect(DB,timeout=10) as c:
        items=[json.loads(x[0]) for x in c.execute('SELECT payload FROM technical_hits WHERE trade_date=? ORDER BY code',(date,)).fetchall()]
    return {'state':dict(TECH),'items':items,'date':date,'note':'需要完整历史K线，扫描是异步渐进的；运行中不得将部分结果称为全市场。Render Free 休眠/重启会中断任务且本地数据库可能丢失。'}

def tech_start_from_source(trade_date):
    with LOCK:
        if TECH['status'] in ('running','collecting'):return dict(TECH)
        TECH.update(day=trade_date,status='collecting',scanned=0,total=0,hits=0,errors=0,error=None,updated_at=now().isoformat(timespec='seconds'))
    def work():
        try:
            df,meta=fetch_eastmoney_all_a()
            if not meta.get('full_market'):
                TECH.update(status='error',error='仅获取部分行情：未达到全A覆盖门槛，不启动全市场筛选',scanned=0,total=meta.get('rows'))
                return
            rows=[]
            for x in df.to_dict('records'):
                code=str(x.get('code') or '')
                name=str(x.get('name') or '')
                if code.startswith(('00','60')) and not any(y in name.upper() for y in ('ST','退')):
                    rows.append({'code':code,'name':name,'float_market_cap':f(x.get('nmc'))*10000})
            tech_start(rows,trade_date)
        except Exception as exc:TECH.update(status='error',error=f'获取全市场真实快照失败：{type(exc).__name__}: {exc}',updated_at=now().isoformat(timespec='seconds'))
    threading.Thread(target=work,daemon=True,name='fullmarket-technical-preflight').start()
    return dict(TECH)
