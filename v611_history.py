"""Strict 30-session historic fields from Eastmoney K-line (f61 turnover rate)."""
from __future__ import annotations
import requests,pandas as pd

def fetch_strict_bars(code,count=45):
    code=str(code).zfill(6)
    if not code.isdigit() or len(code)!=6:raise ValueError('invalid code')
    secid=('1.' if code.startswith('6') else '0.')+code
    params={'secid':secid,'klt':'101','fqt':'1','beg':'0','end':'20500101','lmt':str(count),
            'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
    errors=[]
    for host in ['https://push2his.eastmoney.com/api/qt/stock/kline/get']:
        try:
            r=requests.get(host,params=params,timeout=4.5,headers={'Referer':'https://quote.eastmoney.com/','User-Agent':'Mozilla/5.0'});r.raise_for_status()
            kl=((r.json().get('data') or {}).get('klines') or [])
            if not kl:raise ValueError('no bars')
            rows=[]
            for line in kl:
                a=line.split(',')
                if len(a)<11:continue
                rows.append({'date':a[0],'open':float(a[1]),'close':float(a[2]),'high':float(a[3]),'low':float(a[4]),
                 'volume':float(a[5]),'amount':float(a[6]),'turnover_rate':float(a[10])})
            df=pd.DataFrame(rows)
            if len(df)<36:raise ValueError('insufficient bars')
            return df,'东方财富真实历史日K与换手率'
        except Exception as exc:errors.append(type(exc).__name__)
    raise RuntimeError('历史30日成交额/换手源不可用:'+','.join(errors))
