from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List
import math, requests, csv, io

CN=ZoneInfo('Asia/Shanghai')
UA={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
SYMS={'vix':'^VIX','vxn':'^VXN','sp500':'^GSPC','nasdaq':'^IXIC','dow':'^DJI'}


def _f(v,d=None):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception:return d


def _chart(symbol:str, rng='1mo', interval='1d') -> Dict[str,Any]:
    url=f'https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol,safe="")}'
    r=requests.get(url,params={'range':rng,'interval':interval,'events':'div,splits'},headers=UA,timeout=5)
    r.raise_for_status(); obj=r.json(); res=((obj.get('chart') or {}).get('result') or [None])[0]
    if not res: raise RuntimeError('empty yahoo chart')
    ts=res.get('timestamp') or []; q=((res.get('indicators') or {}).get('quote') or [{}])[0]
    closes=q.get('close') or []; rows=[]
    for t,c in zip(ts,closes):
        c=_f(c)
        if c is None: continue
        rows.append({'ts':int(t),'date':datetime.fromtimestamp(int(t),CN).date().isoformat(),'close':round(c,4)})
    return {'symbol':symbol,'rows':rows,'meta':res.get('meta') or {}}



def _cboe_vol(index:str) -> Dict[str,Any]:
    url=f'https://cdn.cboe.com/api/global/us_indices/daily_prices/{index.upper()}_History.csv'
    r=requests.get(url,headers=UA,timeout=5);r.raise_for_status()
    rows=[]
    for z in csv.DictReader(io.StringIO(r.text)):
        try:
            close=_f(z.get('CLOSE') or z.get('Close'))
            date=str(z.get('DATE') or z.get('Date') or '')
            if close is not None and date: rows.append({'date':date,'close':round(close,4)})
        except Exception: pass
    if not rows: raise RuntimeError('empty cboe csv')
    return {'symbol':index.upper(),'rows':rows[-40:],'meta':{'source':'Cboe daily history'}}

def _summary(x:Dict[str,Any])->Dict[str,Any]:
    rows=x.get('rows') or []
    if not rows:return {'last':None,'change_pct':None,'date':None}
    last=rows[-1]; prev=rows[-2] if len(rows)>=2 else None
    ch=(last['close']/prev['close']-1)*100 if prev and prev.get('close') else None
    return {'last':round(last['close'],2),'change_pct':round(ch,2) if ch is not None else None,'date':last['date'],'history':rows[-8:]}


def _fear_level(v:float|None, tech=False):
    if v is None:return '未知'
    if tech:
        if v<18:return '低波动'
        if v<25:return '常态'
        if v<35:return '偏高'
        return '高压'
    if v<15:return '低波动'
    if v<20:return '常态'
    if v<30:return '偏高'
    return '高压'


def build_global_risk(a_sentiment:float|None=None, a_stage:str='') -> Dict[str,Any]:
    data={}; errors=[]
    for k,s in SYMS.items():
        try:data[k]=_summary(_chart(s)); data[k]['source']='Yahoo Finance chart'
        except Exception as e:
            if k in ('vix','vxn'):
                try:data[k]=_summary(_cboe_vol(k));data[k]['source']='Cboe daily history';continue
                except Exception as e2:errors.append(f'{k}:{type(e).__name__}/{type(e2).__name__}')
            else:errors.append(f'{k}:{type(e).__name__}')
            data[k]={'last':None,'change_pct':None,'date':None,'history':[],'source':None}
    vix=data['vix'].get('last'); vxn=data['vxn'].get('last')
    spread=(vxn-vix) if vix is not None and vxn is not None else None
    notes=[]
    if vix is not None: notes.append(f'VIX {vix:.2f}（{_fear_level(vix)}）')
    if vxn is not None: notes.append(f'VXN {vxn:.2f}（{_fear_level(vxn,True)}）')
    if spread is not None:
        if spread>=7: notes.append('VXN显著高于VIX，科技成长波动溢价偏高')
        elif spread<=2: notes.append('VXN与VIX差距较小，科技波动溢价有限')
        else: notes.append('VXN-VIX处于中等差值区间')
    if a_sentiment is not None:
        if a_sentiment>=65 and (vix or 0)<20: notes.append('A股情绪偏强且美股波动率不高，风险偏好环境相对一致')
        elif a_sentiment>=65 and (vix or 0)>=25: notes.append('A股情绪偏强但海外波动率偏高，内外风险偏好出现背离')
        elif a_sentiment<45 and (vix or 0)>=25: notes.append('A股情绪偏弱且海外波动偏高，风险环境同步承压')
        else: notes.append('A股与海外波动环境暂无明显同向共振')
    return {'ok':not errors or any(data[k].get('last') is not None for k in data),'updated_at':datetime.now(CN).isoformat(timespec='seconds'),
            'a_share':{'sentiment_score':a_sentiment,'stage':a_stage},'markets':data,'vxn_vix_spread':round(spread,2) if spread is not None else None,
            'analysis':notes,'errors':errors,'source':'VIX/VXN优先Yahoo，失败时使用Cboe每日历史；美股指数使用Yahoo chart；A股情绪来自本站实时/收盘情绪模型',
            'note':'VIX/VXN用于波动率环境对照，不代表A股方向预测或上涨概率。'}
