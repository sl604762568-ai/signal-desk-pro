"""Published Eastmoney龙虎榜 summary. Seat types cannot be inferred from anonymous aggregate flows."""
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
CN=ZoneInfo('Asia/Shanghai')
HOSTS=['https://datacenter-web.eastmoney.com/api/data/v1/get','https://datacenter.eastmoney.com/securities/api/data/v1/get']
HEADERS={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/stock/lhb.html'}

def _float(x):
    try:return float(x)
    except (ValueError,TypeError):return None

def fetch_dragons(trade_date=None):
    date=trade_date or datetime.now(CN).date().isoformat()
    args={'reportName':'RPT_DAILYBILLBOARD_DETAILSNEW', 'columns':'ALL','pageNumber':'1','pageSize':'500',
          'sortColumns':'BILLBOARD_NET_AMT','sortTypes':'-1','source':'WEB','client':'WEB',
          'filter':f"(TRADE_DATE>='{date}')(TRADE_DATE<='{date}')"}
    errors=[];items=[];src=None
    for host in HOSTS:
        try:
            resp=requests.get(host,params=args,headers=HEADERS,timeout=5);resp.raise_for_status()
            data=(resp.json().get('result') or {}).get('data') or []
            if not isinstance(data,list):raise ValueError('龙虎榜返回类型错误')
            items=data;src=host;break
        except Exception as exc:errors.append(type(exc).__name__)
    if src is None:
        return {'date':date,'stocks':[],'available':False,'error':'龙虎榜官方披露数据源暂不可用','status':'unavailable'}
    stocks=[]
    for r in items:
        code=str(r.get('SECURITY_CODE') or '')
        if len(code)!=6 or not code.isdigit():continue
        stocks.append({'code':code,'name':r.get('SECURITY_NAME_ABBR'),
            'reason':r.get('EXPLAIN'),'pct':_float(r.get('CHANGE_RATE')),
            'net':_float(r.get('BILLBOARD_NET_AMT')),'buy':_float(r.get('BILLBOARD_BUY_AMT')),
            'sell':_float(r.get('BILLBOARD_SELL_AMT')),
            'turnover':_float(r.get('TURNOVERRATE')),
            'institution_net':None,'hotmoney_net':None,'quant_net':None,'northbound_net':None,
            'institution_resonance':None,'famous_hotmoney':[],
            'fund_classification':'仅披露席位汇总；未核实逐席位主体，不推断游资/机构/量化'} )
    return {'date':date,'stocks':stocks,'available':True,'status':'published','source':'东方财富龙虎榜公开汇总',
            'count':len(stocks),'updated_at':datetime.now(CN).isoformat(timespec='seconds'),
            'classification_note':'游资、机构、量化、北向净额需要可靠逐席位披露和可追溯席位映射。未核实的类别不填数；北向也不等于龙虎榜席位。',
            'tomorrow_plan':'对有龙虎榜披露的股票观察次日集合竞价、所在题材强度、净额是否持续；无披露不推断资金流。'}
