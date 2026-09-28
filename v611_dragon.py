"""Eastmoney 龙虎榜公开汇总 + 逐席位明细。席位标签只按公开营业部名称关键词标注，不推断自然人身份。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
CN=ZoneInfo("Asia/Shanghai")
HOSTS=["https://datacenter-web.eastmoney.com/api/data/v1/get","https://datacenter.eastmoney.com/securities/api/data/v1/get"]
HEADERS={"User-Agent":"Mozilla/5.0","Referer":"https://data.eastmoney.com/stock/lhb.html"}
KNOWN_SEAT_TERMS=("紫阳东路","桑田路","上塘路","金开大道","湖里大道","朱雀大街","解放南路","北京呼家楼","国贸大道","劳动西路","上海溧阳路","上海牡丹江路","深圳益田路","成都系","佛山系")
QUANT_TERMS=("量化","基金专用","量化基金")
NORTH_TERMS=("沪股通专用","深股通专用")

def _float(x):
    try:return float(x)
    except (ValueError,TypeError):return None

def _query(report, filt, page_size=500, sort_columns='', sort_types='-1'):
    args={'reportName':report,'columns':'ALL','pageNumber':'1','pageSize':str(page_size),'source':'WEB','client':'WEB','filter':filt}
    if sort_columns:args.update(sortColumns=sort_columns,sortTypes=sort_types)
    errs=[]
    for host in HOSTS:
        try:
            r=requests.get(host,params=args,headers=HEADERS,timeout=5);r.raise_for_status();return ((r.json().get('result') or {}).get('data') or []),host
        except Exception as e:errs.append(type(e).__name__)
    raise RuntimeError('/'.join(errs) or 'datacenter unavailable')

def _tag(name, code=''):
    name=str(name or '');code=str(code or '')
    if code=='0' or '机构专用' in name:return '机构'
    if any(x in name for x in NORTH_TERMS):return '北向'
    if any(x in name for x in QUANT_TERMS):return '量化'
    if any(x in name for x in KNOWN_SEAT_TERMS):return '知名席位'
    return '营业部'

def _details(code,date):
    out={'buy':[],'sell':[]}; raw=[]
    for report,side,sortcol in [('RPT_BILLBOARD_DAILYDETAILSBUY','buy','BUY'),('RPT_BILLBOARD_DAILYDETAILSSELL','sell','SELL')]:
        try:data,_=_query(report,f"(TRADE_DATE='{date}')(SECURITY_CODE=\"{code}\")",20,sortcol,'-1')
        except Exception:data=[]
        for r in data[:8]:
            row={'name':r.get('OPERATEDEPT_NAME',''),'code':str(r.get('OPERATEDEPT_CODE','')),'buy':_float(r.get('BUY')) or 0,'sell':_float(r.get('SELL')) or 0,'net':_float(r.get('NET')) or 0}
            row['tag']=_tag(row['name'],row['code']);out[side].append(row);raw.append(row)
    inst=sum(x['net'] for x in raw if x['tag']=='机构')
    hm=sum(x['net'] for x in raw if x['tag']=='知名席位')
    quant=sum(x['net'] for x in raw if x['tag']=='量化')
    north=sum(x['net'] for x in raw if x['tag']=='北向')
    hot=[x for x in raw if x['tag']=='知名席位' and x['net']>0]
    return {**out,'institution_net':inst,'hotmoney_net':hm,'quant_net':quant,'northbound_net':north,'famous_hotmoney':hot[:8],
            'institution_resonance':bool(inst>0 and hm>0),'hotmoney_coalition':bool(len({x['name'] for x in hot})>=2)}

def fetch_dragons(trade_date=None):
    date=trade_date or datetime.now(CN).date().isoformat()
    try:items,src=_query('RPT_DAILYBILLBOARD_DETAILSNEW',f"(TRADE_DATE>='{date}')(TRADE_DATE<='{date}')",500,'BILLBOARD_NET_AMT','-1')
    except Exception as e:return {'date':date,'stocks':[],'available':False,'error':'龙虎榜公开汇总暂不可用','detail':str(e)}
    # 当日披露通常在盘后分批更新；若指定日为空，自动退回最近一个有公开披露的交易日。
    actual=date
    if not items:
        d=datetime.fromisoformat(date).date()
        for i in range(1,8):
            x=d-timedelta(days=i)
            if x.weekday()>=5:continue
            ds=x.isoformat()
            try:prev,_=_query('RPT_DAILYBILLBOARD_DETAILSNEW',f"(TRADE_DATE>='{ds}')(TRADE_DATE<='{ds}')",500,'BILLBOARD_NET_AMT','-1')
            except Exception:continue
            if prev:items=prev;actual=ds;break
    base=[]
    for r in items:
        code=str(r.get('SECURITY_CODE') or '')
        if len(code)!=6 or not code.isdigit():continue
        base.append({'code':code,'name':r.get('SECURITY_NAME_ABBR'),'reason':r.get('EXPLAIN') or r.get('EXPLANATION'),'pct':_float(r.get('CHANGE_RATE')),'net':_float(r.get('BILLBOARD_NET_AMT')),'buy':_float(r.get('BILLBOARD_BUY_AMT')),'sell':_float(r.get('BILLBOARD_SELL_AMT')),'turnover':_float(r.get('TURNOVERRATE'))})
    # Only enrich the most relevant rows to avoid hammering the provider.
    enriched={}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs={ex.submit(_details,x['code'],actual):x['code'] for x in base[:18]}
        for f in as_completed(futs):
            try:enriched[futs[f]]=f.result()
            except Exception:pass
    stocks=[]
    for x in base:
        d=enriched.get(x['code']) or {}
        x.update(d)
        
        if x.get('institution_resonance') and x.get('hotmoney_coalition'):
            x['tomorrow_plan']='机构与已标记知名营业部同向净买，且存在多席位合力；次日重点核对竞价强度、题材延续和高开承接。'
        elif x.get('institution_resonance'):
            x['tomorrow_plan']='机构与已标记知名营业部同向净买；次日观察竞价、板块强度与承接是否继续共振。'
        elif x.get('hotmoney_coalition'):
            x['tomorrow_plan']='多个已标记知名营业部呈合力净买；次日关注是否出现一致性过强后的兑现风险。'
        else:
            x['tomorrow_plan']='观察次日竞价强弱、题材延续和席位资金是否出现一致性；席位标签仅按公开营业部名称关键词，不代表自然人身份。'
        stocks.append(x)
    return {'date':actual,'requested_date':date,'stocks':stocks,'available':True,'status':'published','source':'东方财富龙虎榜公开汇总+逐席位明细','count':len(stocks),'updated_at':datetime.now(CN).isoformat(timespec='seconds'),
            'classification_note':'机构依据OPERATEDEPT_CODE=0/机构专用；北向依据沪股通/深股通专用；知名席位仅按公开营业部名称关键词标记，不推断具体个人。',
            'tomorrow_plan':'优先观察机构与知名席位同向净买、多个知名席位合力、且题材处于增强/延续阶段的标的。'}
