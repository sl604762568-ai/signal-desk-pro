"""Real membership-only 09:25 theme aggregates and no-lookahead exploratory replay."""
import json,sqlite3,threading, time
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
from zoneinfo import ZoneInfo
from v611_features import DB, now, auction_view, f
from sector_engine import fetch_stock_sector_info
from public_sources import limit_pct
STATE={'state':'idle','day':None,'mapped':0,'total':0,'error':None}
LOCK=threading.Lock()

def init():
    with sqlite3.connect(DB,timeout=10) as c:
        c.execute('CREATE TABLE IF NOT EXISTS auction_topics(trade_date TEXT PRIMARY KEY,payload TEXT NOT NULL,captured_at TEXT NOT NULL)')

def build_topics(day,news=None,max_stocks=80):
    """Independent web requests; only observed provider memberships contribute."""
    with LOCK:
        if STATE['state']=='running':return dict(STATE)
        STATE.update(state='running',day=day,mapped=0,total=0,error=None)
    def runner():
        try:
            # Do not require yesterday baseline to identify today's *observed* concentration.
            raw=auction_view({'trade_date':day,'sort':'amount','limit':200})
            items=[x for x in raw['items'] if x['amount']>=5000000 and x['gap'] is not None and x['gap']>=0]
            items=items[:max_stocks]; STATE['total']=len(items)
            if not items:
                STATE.update(state='unavailable',error='无真实09:25竞价异动记录');return
            data=[];fails=[]
            with ThreadPoolExecutor(max_workers=3) as pool:
                jobs={pool.submit(fetch_stock_sector_info,x['code']):x for x in items}
                for job in as_completed(jobs):
                    x=jobs[job]
                    try:
                        profile=job.result()
                        memberships=[]
                        if profile.get('industry'):memberships.append(profile['industry'])
                        memberships.extend(z.get('name') for z in profile.get('concepts') or [] if z.get('name'))
                        memberships=list(dict.fromkeys(memberships))
                        if not memberships:fails.append(x['code']);continue
                        data.append((x,profile,memberships))
                        STATE['mapped']+=1
                    except Exception:fails.append(x['code'])
            topics=defaultdict(list)
            for x,profile,memberships in data:
                cap=limit_pct(x['code'],profile.get('name') or '')
                for sector in memberships:
                    topics[sector].append({**x,'name':profile.get('name') or x['code'],
                       'limit_open':x['gap'] is not None and x['gap']>=cap-.15})
            result=[]
            for topic,rows in topics.items():
                total=sum(f(x['amount']) for x in rows)
                core=max(rows,key=lambda x:f(x['amount']))
                # News correlation = titles that mention the exact documented board name; no causal claims.
                headlines=[{'title':i.get('title'),'url':i.get('url'),'source':i.get('source')}
                   for i in (news or {}).get('items',[]) if topic in str(i.get('title',''))][:4]
                result.append({'topic':topic,'amount':total,'stock_count':len(rows),
                  'high_open_count':sum(f(x['gap'])>=1 for x in rows),
                  'limit_open_count':sum(bool(x['limit_open']) for x in rows),
                  'average_gap':round(sum(f(x['gap']) for x in rows)/len(rows),3),
                  'core':{'code':core['code'],'name':core['name'],'amount':core['amount']},
                  'core_share_pct':round(f(core['amount'])/total*100,2) if total else None,
                  'stocks':[{'code':x['code'],'name':x['name'],'amount':x['amount'],'gap':x['gap']} for x in rows],
                  'related_news':headlines,'attribution':'真实板块归属；相关新闻仅说明时间相关，因果未确认'})
            result.sort(key=lambda x:(x['stock_count'],x['amount']),reverse=True)
            payload={'date':day,'topics':result[:100],'mapped_stocks':len(data),
                     'sampled_stocks':len(items),'unmapped_codes':fails,
                     'scope':'按已核实的异动股票真实行业/概念关系聚合；跨题材股票可出现多次，不等于市场全部竞价额',
                     'generated_at':now().isoformat(timespec='seconds')}
            with sqlite3.connect(DB,timeout=10) as c:
                c.execute('INSERT OR REPLACE INTO auction_topics VALUES(?,?,?)',(day,json.dumps(payload,ensure_ascii=False),payload['generated_at']))
            STATE.update(state='complete',mapped=len(data),total=len(items))
        except Exception as exc:STATE.update(state='error',error=f'{type(exc).__name__}: {exc}')
    threading.Thread(target=runner,daemon=True,name='auction-theme-reconciliation').start()
    return dict(STATE)

def get_topics(day):
    with sqlite3.connect(DB,timeout=10) as c:
        row=c.execute('SELECT payload FROM auction_topics WHERE trade_date=?',(day,)).fetchone()
    return {**json.loads(row[0]),'state':'complete'} if row else {'date':day,'topics':[],'state':dict(STATE)}

def replay_real_archive(min_amount=5000000,min_boom=2,min_turnover=.15,min_gap=1,max_gap=7,
                        max_price=80,min_prev_day_pct=2,days=90):
    """Archived, point-in-time signal replay. Illustrative price-path study, not verified executable fills."""
    with sqlite3.connect(DB,timeout=10) as c:
        dates=[r[0] for r in c.execute('SELECT DISTINCT trade_date FROM auction25 ORDER BY trade_date DESC LIMIT ?',(days+1,))][::-1]
        close_dates=[r[0] for r in c.execute('SELECT DISTINCT trade_date FROM daily_close ORDER BY trade_date')]
        close={(r[0],r[1]):r[2] for r in c.execute('SELECT trade_date,code,price FROM daily_close')}
    events=[];daily=[];equity=1.;peak=1.;max_dd=0.;missing=0
    for date in dates[:-1]:
        later=next((x for x in close_dates if x>date),None)
        if not later:missing+=1;continue
        snapshot=auction_view({'trade_date':date,'min_amount':min_amount,'min_boom':min_boom,
          'min_turnover':min_turnover,'min_gap':min_gap,'max_gap':max_gap,'max_price':max_price,
          'min_prev_day_pct':min_prev_day_pct,'limit':200,'sort':'amount'})
        eligible=[x for x in snapshot['items'] if x['passes_available_filters'] and x['complete_for_default']][:5]
        returns=[]
        for x in eligible:
            exitprice=close.get((later,x['code']))
            if not exitprice or x['price']<=0:continue
            # 0.2% round-trip hypothetical friction; not a guaranteed executable auction price.
            ret=(f(exitprice)/x['price']-1)*100-.2
            returns.append(ret)
            events.append({'date':date,'code':x['code'],'auction_price':x['price'],'next_close_date':later,
             'next_close':exitprice,'hypothetical_return_pct':round(ret,3)})
        if returns:
            avg=sum(returns)/len(returns)
            equity*=1+avg/100;peak=max(peak,equity);max_dd=max(max_dd,100*(1-equity/peak))
            daily.append({'date':date,'selections':len(returns),'average_return_pct':round(avg,3),'equity':round(equity,5)})
    wins=sum(e['hypothetical_return_pct']>0 for e in events)
    return {'archive_days':len(dates),'eligible_days':len(daily),'missing_next_close_days':missing,
      'signal_count':len(events),'historical_positive_fraction_pct':round(wins/len(events)*100,2) if events else None,
      'mean_hypothetical_return_pct':round(sum(x['hypothetical_return_pct'] for x in events)/len(events),3) if events else None,
      'max_model_drawdown_pct':round(max_dd,2) if events else None,'daily':daily,'events':events[-120:],
      'method':'09:25已归档实际竞价快照→假设竞价价成交→下一交易日已归档真实收盘价退出；假设往返成本0.2%。不使用事后数据筛选当日信号；无法核实实际成交、排队、滑点，属于假设价格路径研究而非真实可交易回测。'}
