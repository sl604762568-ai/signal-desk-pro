from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo('Asia/Shanghai')

DEFAULTS = {
    'initial_capital': 10000.0,
    'max_positions': 3,
    'position_pct': 0.33,
    'stop_loss_pct': 0.05,
    'take_profit_pct': 0.08,
    'trailing_stop_pct': 0.04,
    'max_hold_days': 5,
    'min_pick_score': 65.0,
    'commission_rate': 0.0003,
    'min_commission': 5.0,
    'stamp_duty_rate': 0.0005,
    'slippage_rate': 0.001,
    'auto_enabled': 1,
}


def _conn(db_path: Path):
    c = sqlite3.connect(db_path, timeout=20)
    c.row_factory = sqlite3.Row
    return c


def init_paper_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn(db_path) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_settings(
            id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_account(
            id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL, initial_capital REAL NOT NULL,
            realized_pnl REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_signals(
            signal_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, rank INTEGER, score REAL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(signal_date, code)
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_positions(
            code TEXT PRIMARY KEY, name TEXT, qty INTEGER NOT NULL, avg_cost REAL NOT NULL,
            entry_price REAL NOT NULL, entry_date TEXT NOT NULL, signal_date TEXT,
            high_water REAL NOT NULL, last_price REAL NOT NULL, last_value REAL NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, trade_date TEXT NOT NULL,
            side TEXT NOT NULL, code TEXT NOT NULL, name TEXT, qty INTEGER NOT NULL,
            price REAL NOT NULL, gross REAL NOT NULL, fees REAL NOT NULL, pnl REAL,
            reason TEXT, signal_date TEXT, payload TEXT
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_equity(
            ts TEXT PRIMARY KEY, trade_date TEXT NOT NULL, cash REAL NOT NULL,
            market_value REAL NOT NULL, equity REAL NOT NULL, pnl REAL NOT NULL,
            return_pct REAL NOT NULL, positions INTEGER NOT NULL
        )''')
        row = conn.execute('SELECT payload FROM paper_settings WHERE id=1').fetchone()
        if not row:
            now = datetime.now(CN_TZ).isoformat(timespec='seconds')
            conn.execute('INSERT INTO paper_settings(id,payload,updated_at) VALUES(1,?,?)',
                         (json.dumps(DEFAULTS, ensure_ascii=False), now))
            conn.execute('INSERT INTO paper_account(id,cash,initial_capital,realized_pnl,updated_at) VALUES(1,?,?,0,?)',
                         (DEFAULTS['initial_capital'], DEFAULTS['initial_capital'], now))
        conn.commit()


def get_settings(db_path: Path) -> Dict[str, Any]:
    init_paper_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute('SELECT payload FROM paper_settings WHERE id=1').fetchone()
    cfg = dict(DEFAULTS)
    if row:
        try: cfg.update(json.loads(row['payload']))
        except Exception: pass
    return cfg


def save_settings(db_path: Path, updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_settings(db_path)
    numeric_limits = {
        'initial_capital': (10000.0, 100000000.0), 'max_positions': (1, 5), 'position_pct': (0.1, 1.0),
        'stop_loss_pct': (0.01, 0.20), 'take_profit_pct': (0.02, 0.50), 'trailing_stop_pct': (0.01, 0.20),
        'max_hold_days': (1, 30), 'min_pick_score': (0, 100), 'commission_rate': (0, 0.01),
        'min_commission': (0, 100), 'stamp_duty_rate': (0, 0.01), 'slippage_rate': (0, 0.02),
    }
    for k, v in updates.items():
        if k == 'auto_enabled':
            cfg[k] = 1 if bool(v) else 0
        elif k in numeric_limits:
            lo, hi = numeric_limits[k]
            try: val = float(v)
            except Exception: continue
            val = max(lo, min(hi, val))
            if k in ('max_positions','max_hold_days'): val = int(round(val))
            cfg[k] = val
    now = datetime.now(CN_TZ).isoformat(timespec='seconds')
    with _conn(db_path) as conn:
        conn.execute('INSERT INTO paper_settings(id,payload,updated_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                     (json.dumps(cfg, ensure_ascii=False), now))
        conn.commit()
    return cfg


def reset_account(db_path: Path, initial_capital: Optional[float] = None) -> Dict[str, Any]:
    cfg = get_settings(db_path)
    capital = float(initial_capital or cfg.get('initial_capital') or 10000)
    capital = max(10000.0, capital)
    cfg['initial_capital'] = capital
    save_settings(db_path, cfg)
    now = datetime.now(CN_TZ).isoformat(timespec='microseconds')
    with _conn(db_path) as conn:
        conn.execute('DELETE FROM paper_positions')
        conn.execute('DELETE FROM paper_trades')
        conn.execute('DELETE FROM paper_equity')
        conn.execute('DELETE FROM paper_signals')
        conn.execute('DELETE FROM paper_account')
        conn.execute('INSERT INTO paper_account(id,cash,initial_capital,realized_pnl,updated_at) VALUES(1,?,?,0,?)', (capital, capital, now))
        conn.execute('INSERT INTO paper_equity(ts,trade_date,cash,market_value,equity,pnl,return_pct,positions) VALUES(?,?,?,?,?,?,?,?)',
                     (now, now[:10], capital, 0.0, capital, 0.0, 0.0, 0))
        conn.commit()
    return get_portfolio(db_path, {})


def store_signals(db_path: Path, signal_date: str, picks: List[Dict[str, Any]]) -> int:
    if not signal_date or not picks: return 0
    now = datetime.now(CN_TZ).isoformat(timespec='seconds')
    count = 0
    with _conn(db_path) as conn:
        for p in picks[:5]:
            code = str(p.get('code','')).zfill(6)
            if len(code) != 6: continue
            conn.execute('''INSERT INTO paper_signals(signal_date,code,name,rank,score,payload,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(signal_date,code) DO UPDATE SET name=excluded.name,rank=excluded.rank,score=excluded.score,payload=excluded.payload,created_at=excluded.created_at''',
                (signal_date, code, str(p.get('name','')), int(p.get('rank') or 99), float(p.get('score') or 0), json.dumps(p, ensure_ascii=False), now))
            count += 1
        conn.commit()
    return count


def latest_signals(db_path: Path, before_date: Optional[str] = None) -> List[Dict[str, Any]]:
    init_paper_db(db_path)
    with _conn(db_path) as conn:
        if before_date:
            row = conn.execute('SELECT MAX(signal_date) d FROM paper_signals WHERE signal_date < ?', (before_date,)).fetchone()
        else:
            row = conn.execute('SELECT MAX(signal_date) d FROM paper_signals').fetchone()
        d = row['d'] if row else None
        if not d: return []
        rows = conn.execute('SELECT * FROM paper_signals WHERE signal_date=? ORDER BY rank, score DESC', (d,)).fetchall()
    out=[]
    for r in rows:
        try: p=json.loads(r['payload'])
        except Exception: p={}
        p['_signal_date']=r['signal_date']; out.append(p)
    return out


def _commission(gross: float, cfg: Dict[str, Any], sell: bool=False) -> float:
    c = max(float(cfg['min_commission']), gross * float(cfg['commission_rate']))
    if sell: c += gross * float(cfg['stamp_duty_rate'])
    return c


def _find_snapshot(market: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    for s in (market.get('review_universe') or market.get('active_stocks') or []):
        if str(s.get('code','')).zfill(6) == code:
            return s
    return None


def _holding_days(entry_date: str, trade_date: str) -> int:
    try:
        a=date.fromisoformat(entry_date[:10]); b=date.fromisoformat(trade_date[:10]); return max(0,(b-a).days)
    except Exception: return 0


def _buy_allowed(p: Dict[str, Any], snap: Dict[str, Any], cfg: Dict[str, Any]) -> tuple[bool,str]:
    if float(p.get('score') or 0) < float(cfg['min_pick_score']): return False, '候选综合分不足'
    if str(p.get('execution_state','')).startswith('观察池'): return False, '市场门槛未通过'
    price=float(snap.get('price') or 0); op=float(snap.get('open') or 0); prev=float(snap.get('prev_close') or 0)
    pct=float(snap.get('pct') or 0)
    if price<=0: return False,'无有效实时价格'
    if pct>6.5: return False,'盘中涨幅过高，避免追高'
    if pct<-3.5: return False,'盘中明显转弱'
    if prev>0 and op>0 and (op/prev-1)*100>4.5: return False,'高开幅度过大'
    support=float(p.get('support') or 0)
    if support>0 and price < support*0.985: return False,'跌破候选支撑位'
    t=float(p.get('technical_score') or 0); s=float(p.get('sector_score') or 0); e=float(p.get('short_term_elasticity') or p.get('volume_price_score') or 0)
    if t<58: return False,'技术面确认不足'
    if s<50: return False,'板块强度不足'
    if e<50: return False,'短线弹性不足'
    return True,'次日候选+实时条件通过'


def run_engine(db_path: Path, market: Dict[str, Any]) -> Dict[str, Any]:
    init_paper_db(db_path)
    cfg=get_settings(db_path)
    now=datetime.now(CN_TZ)
    trade_date=str(market.get('trade_date') or now.date().isoformat())[:10]
    actions=[]
    with _conn(db_path) as conn:
        acc=conn.execute('SELECT * FROM paper_account WHERE id=1').fetchone()
        cash=float(acc['cash']); initial=float(acc['initial_capital']); realized=float(acc['realized_pnl'])
        positions=[dict(r) for r in conn.execute('SELECT * FROM paper_positions').fetchall()]

        # 1) exits first
        for pos in positions:
            code=pos['code']; snap=_find_snapshot(market,code)
            if not snap: continue
            px=float(snap.get('price') or 0)
            if px<=0: continue
            high=max(float(pos['high_water']),px)
            payload=json.loads(pos['payload'] or '{}')
            stop=float(cfg['stop_loss_pct']); take=float(cfg['take_profit_pct']); trail=float(cfg['trailing_stop_pct'])
            reason=None
            if px <= float(pos['avg_cost'])*(1-stop): reason=f'止损 {stop*100:.1f}%'
            elif px >= float(pos['avg_cost'])*(1+take): reason=f'止盈 {take*100:.1f}%'
            elif high >= float(pos['avg_cost'])*1.04 and px <= high*(1-trail): reason=f'移动止盈回撤 {trail*100:.1f}%'
            elif _holding_days(pos['entry_date'],trade_date) >= int(cfg['max_hold_days']): reason=f'持仓达到 {int(cfg["max_hold_days"])} 天'
            else:
                support=float(payload.get('support') or 0); ma20=float((payload.get('technical') or {}).get('ma20') or 0)
                key=max(support,ma20)
                if key>0 and px < key*0.985: reason='跌破关键支撑/MA20'
            if reason:
                fill=px*(1-float(cfg['slippage_rate'])); gross=fill*int(pos['qty']); fees=_commission(gross,cfg,True)
                pnl=gross-fees-float(pos['avg_cost'])*int(pos['qty'])
                cash += gross-fees; realized += pnl
                conn.execute('DELETE FROM paper_positions WHERE code=?',(code,))
                conn.execute('''INSERT INTO paper_trades(ts,trade_date,side,code,name,qty,price,gross,fees,pnl,reason,signal_date,payload)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(now.isoformat(timespec='seconds'),trade_date,'SELL',code,pos['name'],pos['qty'],fill,gross,fees,pnl,reason,pos['signal_date'],pos['payload']))
                actions.append({'side':'SELL','code':code,'name':pos['name'],'qty':pos['qty'],'price':round(fill,3),'pnl':round(pnl,2),'reason':reason})
            else:
                conn.execute('UPDATE paper_positions SET high_water=?,last_price=?,last_value=?,updated_at=? WHERE code=?',
                             (high,px,px*int(pos['qty']),now.isoformat(timespec='seconds'),code))

        positions=[dict(r) for r in conn.execute('SELECT * FROM paper_positions').fetchall()]
        held={p['code'] for p in positions}

        # 2) entries use most recent prior-day selection
        signals=latest_signals(db_path,before_date=trade_date)
        slots=max(0,int(cfg['max_positions'])-len(positions))
        for p in signals:
            if slots<=0: break
            code=str(p.get('code','')).zfill(6)
            if code in held: continue
            used=conn.execute('SELECT 1 FROM paper_trades WHERE side="BUY" AND code=? AND signal_date=? LIMIT 1',(code,p.get('_signal_date'))).fetchone()
            if used: continue
            snap=_find_snapshot(market,code)
            if not snap: continue
            ok,reason=_buy_allowed(p,snap,cfg)
            if not ok: continue
            px=float(snap.get('price') or 0)*(1+float(cfg['slippage_rate']))
            if px<=0: continue
            equity_budget=max(0,initial+realized)
            budget=min(cash, max(0,equity_budget*float(cfg['position_pct'])))
            # A股按100股一手模拟
            qty=int(math.floor(budget/(px*100))*100)
            if qty<100:
                continue
            gross=px*qty; fees=_commission(gross,cfg,False); total=gross+fees
            if total>cash:
                qty=int(math.floor((cash-float(cfg['min_commission']))/(px*100))*100)
                if qty<100: continue
                gross=px*qty; fees=_commission(gross,cfg,False); total=gross+fees
            cash-=total
            avg_cost=total/qty
            payload=json.dumps(p,ensure_ascii=False)
            conn.execute('''INSERT OR REPLACE INTO paper_positions(code,name,qty,avg_cost,entry_price,entry_date,signal_date,high_water,last_price,last_value,payload,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(code,str(p.get('name','')),qty,avg_cost,px,trade_date,p.get('_signal_date'),px,px,gross,payload,now.isoformat(timespec='seconds')))
            conn.execute('''INSERT INTO paper_trades(ts,trade_date,side,code,name,qty,price,gross,fees,pnl,reason,signal_date,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(now.isoformat(timespec='seconds'),trade_date,'BUY',code,str(p.get('name','')),qty,px,gross,fees,None,reason,p.get('_signal_date'),payload))
            actions.append({'side':'BUY','code':code,'name':p.get('name'),'qty':qty,'price':round(px,3),'reason':reason})
            held.add(code); slots-=1

        # 3) account + equity snapshot
        positions=[dict(r) for r in conn.execute('SELECT * FROM paper_positions').fetchall()]
        mv=0.0
        for pos in positions:
            snap=_find_snapshot(market,pos['code']); px=float((snap or {}).get('price') or pos['last_price'] or 0)
            mv += px*int(pos['qty'])
        equity=cash+mv
        ret=(equity/initial-1)*100 if initial else 0
        conn.execute('UPDATE paper_account SET cash=?,realized_pnl=?,updated_at=? WHERE id=1',(cash,realized,now.isoformat(timespec='seconds')))
        conn.execute('''INSERT OR REPLACE INTO paper_equity(ts,trade_date,cash,market_value,equity,pnl,return_pct,positions) VALUES(?,?,?,?,?,?,?,?)''',
                     (now.isoformat(timespec='microseconds'),trade_date,cash,mv,equity,equity-initial,ret,len(positions)))
        conn.commit()
    return {'ok':True,'trade_date':trade_date,'actions':actions,'portfolio':get_portfolio(db_path,market)}


def get_portfolio(db_path: Path, market: Dict[str, Any]) -> Dict[str, Any]:
    init_paper_db(db_path); cfg=get_settings(db_path)
    with _conn(db_path) as conn:
        acc=conn.execute('SELECT * FROM paper_account WHERE id=1').fetchone()
        positions=[dict(r) for r in conn.execute('SELECT * FROM paper_positions ORDER BY entry_date, code').fetchall()]
        trades=[dict(r) for r in conn.execute('SELECT * FROM paper_trades ORDER BY id DESC LIMIT 100').fetchall()]
        eq=[dict(r) for r in conn.execute('SELECT * FROM paper_equity ORDER BY ts DESC LIMIT 500').fetchall()]
    for t in trades:
        try:
            payload=json.loads(t.get('payload') or '{}')
        except Exception:
            payload={}
        reasons=payload.get('reasons') or []
        risks=payload.get('risks') or []
        tech=payload.get('technical') or {}
        t['decision_reason']=t.get('reason') or ('；'.join(reasons[:3]) if reasons else '策略条件触发')
        parts=[]
        if payload.get('technical_score') is not None: parts.append(f"技术分 {float(payload.get('technical_score') or 0):.0f}")
        if tech.get('macd') is not None: parts.append(f"MACD {float(tech.get('macd') or 0):.2f}")
        if tech.get('ma20') is not None: parts.append(f"MA20 {float(tech.get('ma20') or 0):.2f}")
        if payload.get('support') is not None: parts.append(f"支撑 {float(payload.get('support') or 0):.2f}")
        if payload.get('resistance') is not None: parts.append(f"压力 {float(payload.get('resistance') or 0):.2f}")
        t['technical_note']=' · '.join(parts) if parts else ('；'.join(reasons[-2:]) if reasons else '无额外技术摘要')
        info=[]
        if payload.get('sector_name'): info.append(f"题材/板块 {payload.get('sector_name')}")
        if payload.get('sector_score') is not None: info.append(f"板块分 {float(payload.get('sector_score') or 0):.0f}")
        if payload.get('market_score') is not None: info.append(f"市场分 {float(payload.get('market_score') or 0):.0f}")
        if reasons: info.extend([str(x) for x in reasons[:2]])
        if risks: info.append('风险：'+str(risks[0]))
        t['info_note']='；'.join(info) if info else '无额外信息面摘要'
    cash=float(acc['cash']); initial=float(acc['initial_capital']); mv=0.0; unreal=0.0
    for p in positions:
        snap=_find_snapshot(market,p['code']) if market else None
        px=float((snap or {}).get('price') or p['last_price'] or 0)
        p['current_price']=px; p['market_value']=round(px*int(p['qty']),2); p['unrealized_pnl']=round((px-float(p['avg_cost']))*int(p['qty']),2)
        p['return_pct']=round((px/float(p['avg_cost'])-1)*100,2) if float(p['avg_cost']) else 0
        mv += p['market_value']; unreal += p['unrealized_pnl']
    equity=cash+mv; total_pnl=equity-initial
    closed=[t for t in trades if t['side']=='SELL' and t['pnl'] is not None]
    wins=[t for t in closed if float(t['pnl'])>0]
    return {'settings':cfg,'cash':round(cash,2),'initial_capital':round(initial,2),'market_value':round(mv,2),'equity':round(equity,2),
            'total_pnl':round(total_pnl,2),'return_pct':round((equity/initial-1)*100,2) if initial else 0,'realized_pnl':round(float(acc['realized_pnl']),2),
            'unrealized_pnl':round(unreal,2),'positions':positions,'trades':trades,'equity_curve':list(reversed(eq)),
            'closed_trades':len(closed),'win_rate':round(len(wins)/len(closed)*100,1) if closed else None,
            'note':'纯虚拟盘，不连接券商、不产生真实订单。A股买入按100股一手模拟。'}


def performance(db_path: Path, days: int = 30) -> Dict[str, Any]:
    init_paper_db(db_path); days=max(1,min(3650,int(days)))
    with _conn(db_path) as conn:
        rows=[dict(r) for r in conn.execute('SELECT * FROM paper_equity ORDER BY ts').fetchall()]
        trades=[dict(r) for r in conn.execute('SELECT * FROM paper_trades ORDER BY id').fetchall()]
    if not rows:
        return {'days':days,'points':[],'trades':[],'stats':{},'note':'尚无模拟成交记录；启用后从每日真实选股开始积累前向回测数据。'}
    cutoff=datetime.now(CN_TZ).date().toordinal()-days
    filt=[]
    for r in rows:
        try:
            if date.fromisoformat(r['trade_date'][:10]).toordinal()>=cutoff: filt.append(r)
        except Exception: pass
    if not filt: filt=rows[-1:]
    start=float(filt[0]['equity']); end=float(filt[-1]['equity'])
    peak=-1e99; maxdd=0.0
    for r in filt:
        e=float(r['equity']); peak=max(peak,e)
        if peak>0: maxdd=min(maxdd,(e/peak-1)*100)
    dates={r['trade_date'] for r in filt}
    closed=[t for t in trades if t['side']=='SELL' and t['trade_date'] in dates and t['pnl'] is not None]
    wins=[t for t in closed if float(t['pnl'])>0]; losses=[t for t in closed if float(t['pnl'])<=0]
    gp=sum(float(t['pnl']) for t in wins); gl=abs(sum(float(t['pnl']) for t in losses))
    return {'days':days,'points':[{'ts':r['ts'],'date':r['trade_date'],'equity':round(float(r['equity']),2),'return_pct':round(float(r['return_pct']),2)} for r in filt],
            'trades':closed[-100:],
            'stats':{'start_equity':round(start,2),'end_equity':round(end,2),'return_pct':round((end/start-1)*100,2) if start else 0,
                     'max_drawdown_pct':round(maxdd,2),'closed_trades':len(closed),'win_rate':round(len(wins)/len(closed)*100,1) if closed else None,
                     'profit_factor':round(gp/gl,2) if gl>0 else (None if gp==0 else 99.0),'net_pnl':round(end-start,2)},
            'note':'这是从启用虚拟盘后真实记录的前向回测/纸面交易绩效；不会倒推不存在的历史“次日5股”信号。'}

def has_signals_for(db_path: Path, signal_date: str) -> bool:
    init_paper_db(db_path)
    with _conn(db_path) as conn:
        row=conn.execute('SELECT 1 FROM paper_signals WHERE signal_date=? LIMIT 1',(signal_date,)).fetchone()
    return bool(row)


def get_watch_codes(db_path: Path, include_signals: bool = True) -> List[str]:
    """Return the small set of symbols that need real-time paper quotes."""
    init_paper_db(db_path)
    codes=[]
    with _conn(db_path) as conn:
        rows=conn.execute('SELECT code FROM paper_positions ORDER BY entry_date, code').fetchall()
        codes.extend(str(r['code']).zfill(6) for r in rows)
    if include_signals:
        for p in latest_signals(db_path):
            c=str(p.get('code','')).zfill(6)
            if len(c)==6:
                codes.append(c)
    return list(dict.fromkeys(codes))[:30]


def _record_equity_manual(conn, cash: float, initial: float, realized: float, market: Dict[str, Any], now: datetime) -> None:
    positions=[dict(r) for r in conn.execute('SELECT * FROM paper_positions').fetchall()]
    mv=0.0
    for pos in positions:
        snap=_find_snapshot(market,pos['code'])
        px=float((snap or {}).get('price') or pos['last_price'] or 0)
        mv += px*int(pos['qty'])
    equity=cash+mv
    ret=(equity/initial-1)*100 if initial else 0.0
    conn.execute('UPDATE paper_account SET cash=?,realized_pnl=?,updated_at=? WHERE id=1',
                 (cash,realized,now.isoformat(timespec='seconds')))
    conn.execute('INSERT OR REPLACE INTO paper_equity(ts,trade_date,cash,market_value,equity,pnl,return_pct,positions) VALUES(?,?,?,?,?,?,?,?)',
                 (now.isoformat(timespec='microseconds'),now.date().isoformat(),cash,mv,equity,equity-initial,ret,len(positions)))


def manual_buy(db_path: Path, quote: Dict[str, Any], qty: Optional[int] = None, amount: Optional[float] = None) -> Dict[str, Any]:
    """Paper-only manual buy using a server-fetched live quote."""
    init_paper_db(db_path); cfg=get_settings(db_path); now=datetime.now(CN_TZ)
    code=str(quote.get('code','')).zfill(6); name=str(quote.get('name',''))
    price=float(quote.get('price') or 0)
    if len(code)!=6 or price<=0:
        return {'ok':False,'error':'无有效实时报价'}
    with _conn(db_path) as conn:
        acc=conn.execute('SELECT * FROM paper_account WHERE id=1').fetchone()
        cash=float(acc['cash']); initial=float(acc['initial_capital']); realized=float(acc['realized_pnl'])
        existing=conn.execute('SELECT * FROM paper_positions WHERE code=?',(code,)).fetchone()
        if not existing:
            count=conn.execute('SELECT COUNT(*) c FROM paper_positions').fetchone()['c']
            if int(count)>=int(cfg['max_positions']):
                return {'ok':False,'error':f'已达到最大持仓数 {int(cfg["max_positions"])}'}
        fill=price*(1+float(cfg['slippage_rate']))
        if qty is not None:
            try: q=int(qty)
            except Exception: q=0
            q=(q//100)*100
        else:
            budget=min(cash, max(0.0,float(amount or 0)))
            q=int(math.floor(budget/(fill*100))*100) if budget>0 else 0
        if q<100:
            return {'ok':False,'error':'买入数量至少100股；若按金额买入，请提高金额'}
        gross=fill*q; fees=_commission(gross,cfg,False); total=gross+fees
        if total>cash:
            return {'ok':False,'error':f'可用现金不足，预计需要 {total:.2f} 元，当前现金 {cash:.2f} 元'}
        cash-=total
        if existing:
            old_qty=int(existing['qty']); old_cost=float(existing['avg_cost'])*old_qty
            new_qty=old_qty+q; avg=(old_cost+total)/new_qty
            payload=existing['payload'] or '{}'
            high=max(float(existing['high_water']),price)
            conn.execute('UPDATE paper_positions SET name=?,qty=?,avg_cost=?,last_price=?,last_value=?,high_water=?,updated_at=? WHERE code=?',
                         (name or existing['name'],new_qty,avg,price,price*new_qty,high,now.isoformat(timespec='seconds'),code))
        else:
            payload=json.dumps({'manual':True,'source':'manual'},ensure_ascii=False)
            conn.execute('INSERT INTO paper_positions(code,name,qty,avg_cost,entry_price,entry_date,signal_date,high_water,last_price,last_value,payload,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (code,name,q,total/q,fill,now.date().isoformat(),None,price,price,price*q,payload,now.isoformat(timespec='seconds')))
        conn.execute('INSERT INTO paper_trades(ts,trade_date,side,code,name,qty,price,gross,fees,pnl,reason,signal_date,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (now.isoformat(timespec='seconds'),now.date().isoformat(),'BUY',code,name,q,fill,gross,fees,None,'手动虚拟买入',None,payload))
        market={'review_universe':[quote],'active_stocks':[quote]}
        _record_equity_manual(conn,cash,initial,realized,market,now)
        conn.commit()
    return {'ok':True,'action':{'side':'BUY','code':code,'name':name,'qty':q,'price':round(fill,3),'gross':round(gross,2),'fees':round(fees,2),'reason':'手动虚拟买入'}}


def manual_sell(db_path: Path, quote: Dict[str, Any], qty: Optional[int] = None, sell_all: bool = False) -> Dict[str, Any]:
    """Paper-only manual sell. Enforces A-share T+1 for positions bought today."""
    init_paper_db(db_path); cfg=get_settings(db_path); now=datetime.now(CN_TZ)
    code=str(quote.get('code','')).zfill(6); price=float(quote.get('price') or 0)
    if len(code)!=6 or price<=0:
        return {'ok':False,'error':'无有效实时报价'}
    with _conn(db_path) as conn:
        acc=conn.execute('SELECT * FROM paper_account WHERE id=1').fetchone()
        cash=float(acc['cash']); initial=float(acc['initial_capital']); realized=float(acc['realized_pnl'])
        pos=conn.execute('SELECT * FROM paper_positions WHERE code=?',(code,)).fetchone()
        if not pos:
            return {'ok':False,'error':'虚拟盘没有该股票持仓'}
        if str(pos['entry_date'])[:10] == now.date().isoformat():
            return {'ok':False,'error':'按A股T+1模拟：今日买入的仓位今日不可卖出'}
        held=int(pos['qty'])
        if sell_all or qty is None:
            q=held
        else:
            try: q=int(qty)
            except Exception: q=0
            q=(q//100)*100
            if q<100:
                return {'ok':False,'error':'卖出数量至少100股'}
            q=min(q,held)
        fill=price*(1-float(cfg['slippage_rate'])); gross=fill*q; fees=_commission(gross,cfg,True)
        cost=float(pos['avg_cost'])*q; pnl=gross-fees-cost
        cash += gross-fees; realized += pnl
        remain=held-q
        if remain<=0:
            conn.execute('DELETE FROM paper_positions WHERE code=?',(code,))
        else:
            conn.execute('UPDATE paper_positions SET qty=?,last_price=?,last_value=?,updated_at=? WHERE code=?',
                         (remain,price,price*remain,now.isoformat(timespec='seconds'),code))
        conn.execute('INSERT INTO paper_trades(ts,trade_date,side,code,name,qty,price,gross,fees,pnl,reason,signal_date,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (now.isoformat(timespec='seconds'),now.date().isoformat(),'SELL',code,pos['name'],q,fill,gross,fees,pnl,'手动虚拟卖出',pos['signal_date'],pos['payload']))
        market={'review_universe':[quote],'active_stocks':[quote]}
        _record_equity_manual(conn,cash,initial,realized,market,now)
        conn.commit()
    return {'ok':True,'action':{'side':'SELL','code':code,'name':pos['name'],'qty':q,'price':round(fill,3),'gross':round(gross,2),'fees':round(fees,2),'pnl':round(pnl,2),'reason':'手动虚拟卖出'}}
