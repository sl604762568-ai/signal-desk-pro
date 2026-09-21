from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import os

CN_TZ = ZoneInfo("Asia/Shanghai")

# Render 等云机房访问部分行情源时，requests 若没有 timeout 可能长时间挂起。
# 给 AKShare 内部所有 requests 请求补一个默认连接/读取超时；显式 timeout 不覆盖。
HTTP_TIMEOUT = float(os.getenv("MARKET_HTTP_TIMEOUT", "6"))
if not getattr(requests.sessions.Session.request, "_signal_desk_timeout_patch", False):
    _orig_session_request = requests.sessions.Session.request
    def _signal_desk_request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (min(3.5, HTTP_TIMEOUT), HTTP_TIMEOUT)
        return _orig_session_request(self, method, url, **kwargs)
    _signal_desk_request._signal_desk_timeout_patch = True
    requests.sessions.Session.request = _signal_desk_request


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _safe_mean(series: Iterable[Any]) -> float:
    vals = [_num(v, math.nan) for v in series]
    vals = [v for v in vals if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else 0.0


def _df_records(df: Optional[pd.DataFrame], limit: int = 100) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: List[Dict[str, Any]] = []
    for row in df.head(limit).to_dict("records"):
        clean = {}
        for k, v in row.items():
            if pd.isna(v):
                clean[str(k)] = None
            elif hasattr(v, "item"):
                clean[str(k)] = v.item()
            else:
                clean[str(k)] = v
        out.append(clean)
    return out


class DemoProvider:
    name = "演示数据"

    def fetch(self, fast: bool = False) -> Dict[str, Any]:
        now = datetime.now(CN_TZ)
        # 小幅随机，保持页面刷新时有动态感。
        zt = random.randint(48, 76)
        zb = random.randint(13, 28)
        dt = random.randint(3, 14)
        seal = round(zt / max(1, zt + zb) * 100, 1)
        up = random.randint(2600, 3600)
        down = random.randint(1300, 2300)
        premium = round(random.uniform(0.5, 3.8), 2)
        max_board = random.choice([5, 6, 6, 7])
        promotions = [
            {"label": "1→2", "numerator": 12, "denominator": 34, "rate": 35.3},
            {"label": "2→3", "numerator": 5, "denominator": 11, "rate": 45.5},
            {"label": "3→4", "numerator": 2, "denominator": 4, "rate": 50.0},
            {"label": "4→5", "numerator": 1, "denominator": 2, "rate": 50.0},
            {"label": "5→6", "numerator": 1, "denominator": 1, "rate": 100.0},
        ]
        ladder = [
            {"board": max_board, "count": 1, "stocks": ["示例龙头A"]},
            {"board": 4, "count": 2, "stocks": ["示例B", "示例C"]},
            {"board": 3, "count": 4, "stocks": ["示例D", "示例E", "示例F"]},
            {"board": 2, "count": 9, "stocks": ["示例G", "示例H", "示例I"]},
        ]
        themes = [
            {"name": "机器人", "limitups": 8, "max_board": 4, "score": 91, "leaders": ["示例B", "示例C"]},
            {"name": "算力", "limitups": 6, "max_board": 3, "score": 82, "leaders": ["示例D"]},
            {"name": "消费电子", "limitups": 5, "max_board": 2, "score": 73, "leaders": ["示例E"]},
            {"name": "电力设备", "limitups": 4, "max_board": 2, "score": 66, "leaders": ["示例F"]},
            {"name": "大金融", "limitups": 3, "max_board": 1, "score": 52, "leaders": ["示例G"]},
        ]
        concepts = [
            {"name": "人形机器人", "pct": 4.2, "up": 54, "down": 7, "leader": "示例B", "leader_pct": 10.0},
            {"name": "CPO", "pct": 3.6, "up": 31, "down": 8, "leader": "示例D", "leader_pct": 9.8},
            {"name": "AI服务器", "pct": 2.9, "up": 38, "down": 12, "leader": "示例E", "leader_pct": 8.7},
        ]
        auction = {"avg_gap": 1.62, "red_ratio": 67.5, "strong_ratio": 25.0, "leaders": [
            {"code": "000001", "name": "示例A", "gap": 6.2},
            {"code": "000002", "name": "示例B", "gap": 4.8},
            {"code": "000003", "name": "示例C", "gap": 3.9},
        ]}
        active_stocks = [
            {"code":"000001","name":"示例科技A","price":18.62,"pct":6.8,"volume_ratio":2.16,"turnover_rate":8.4,"amount":9.6e8,"high":18.88,"low":17.25,"open":17.54,"prev_close":17.43,"industry":"机器人"},
            {"code":"000002","name":"示例算力B","price":31.40,"pct":5.2,"volume_ratio":1.74,"turnover_rate":6.1,"amount":14.2e8,"high":31.62,"low":29.65,"open":29.92,"prev_close":29.85,"industry":"通信设备"},
            {"code":"000003","name":"示例芯片C","price":42.18,"pct":4.6,"volume_ratio":2.48,"turnover_rate":10.2,"amount":11.8e8,"high":42.50,"low":40.12,"open":40.35,"prev_close":40.34,"industry":"半导体"},
            {"code":"000004","name":"示例电力D","price":12.76,"pct":3.9,"volume_ratio":1.43,"turnover_rate":4.9,"amount":7.1e8,"high":12.82,"low":12.11,"open":12.20,"prev_close":12.28,"industry":"电力设备"},
            {"code":"000005","name":"示例汽车E","price":26.33,"pct":7.2,"volume_ratio":3.10,"turnover_rate":12.8,"amount":16.4e8,"high":26.58,"low":24.10,"open":24.35,"prev_close":24.56,"industry":"汽车零部件"},
        ]
        limitup_stocks = [
            {"code":"000001","name":"示例科技A","pct":10.0,"industry":"机器人","board":4,"seal_amount":4.8e8},
            {"code":"000006","name":"示例龙头F","pct":10.0,"industry":"通信设备","board":3,"seal_amount":6.1e8},
        ]
        return {
            "source": self.name,
            "is_live": False,
            "trade_date": now.strftime("%Y-%m-%d"),
            "updated_at": now.isoformat(timespec="seconds"),
            "market_status": _market_status(now),
            "zt_count": zt,
            "zb_count": zb,
            "dt_count": dt,
            "seal_rate": seal,
            "yesterday_premium": premium,
            "yesterday_lianban_premium": round(premium + 1.1, 2),
            "up_count": up,
            "down_count": down,
            "flat_count": 280,
            "turnover": 1.42e12,
            "max_board": max_board,
            "promotion_rates": promotions,
            "ladder": ladder,
            "themes": themes,
            "concepts": concepts,
            "auction": auction,
            "active_stocks": active_stocks,
            "review_universe": active_stocks,
            "limitup_stocks": limitup_stocks,
        }



class DirectPublicProvider:
    """No-token provider: Sina all-A snapshot + Tencent/Sina daily history fallback.

    This deliberately bypasses AKShare for the live core so a blocked upstream adapter cannot
    prevent the dashboard from updating. Public web endpoints are best-effort and the payload
    always carries source/coverage metadata.
    """
    name = "新浪实时快照 + 腾讯K线"

    def __init__(self):
        from public_sources import fetch_history_df
        self.history_fetcher = fetch_history_df

    def _latest_trade_date(self) -> str:
        now = datetime.now(CN_TZ)
        while now.weekday() >= 5:
            now -= timedelta(days=1)
        return now.strftime("%Y%m%d")

    def fetch(self, fast: bool = False) -> Dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from public_sources import fetch_full_market_snapshot, fetch_sina_fast_snapshot, fetch_history_df, limit_pct

        now = datetime.now(CN_TZ)
        date = self._latest_trade_date()
        # Even the fast dashboard path uses a *complete* market snapshot when possible.
        # Partial ranked slices are allowed only as a last-resort fallback and are never
        # used for full-market breadth / limit-up statistics.
        try:
            spot, meta = fetch_full_market_snapshot()
        except Exception:
            spot, meta = fetch_sina_fast_snapshot()
        if spot is None or spot.empty or "code" not in spot.columns:
            raise RuntimeError("新浪全A快照无可用数据")

        # Normalize fields from Sina's direct market-center endpoint.
        rename = {
            "code":"代码", "name":"名称", "trade":"最新价", "changepercent":"涨跌幅",
            "settlement":"昨收", "open":"今开", "high":"最高", "low":"最低",
            "volume":"成交量", "amount":"成交额", "turnoverratio":"换手率",
            "mktcap":"总市值", "nmc":"流通市值", "volume_ratio":"量比",
        }
        spot = spot.rename(columns={k:v for k,v in rename.items() if k in spot.columns}).copy()
        spot["代码"] = spot["代码"].astype(str).str.zfill(6)
        for col in ["最新价","涨跌幅","昨收","今开","最高","最低","成交量","成交额","换手率","量比","总市值","流通市值"]:
            if col in spot.columns:
                spot[col] = pd.to_numeric(spot[col], errors="coerce")
            else:
                spot[col] = 0.0
        if "名称" not in spot.columns:
            spot["名称"] = ""

        full_market = bool(meta.get("full_market"))
        pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
        if full_market:
            up_count = int((pct > 0).sum()); down_count = int((pct < 0).sum()); flat_count = int((pct == 0).sum())
        else:
            up_count = down_count = flat_count = None
        turnover = float(pd.to_numeric(spot["成交额"], errors="coerce").fillna(0).sum()) if full_market else 0.0

        # Reconstruct daily price-limit states from *price levels*, not from a loose
        # percentage threshold.  This avoids falsely counting IPO/no-limit stocks whose
        # daily return happens to be >10%.  A-share limit prices are rounded to ¥0.01.
        from decimal import Decimal, ROUND_HALF_UP
        def _limit_price(prev: float, pct: float, direction: int) -> float:
            if prev <= 0: return 0.0
            factor = Decimal("1") + (Decimal(str(pct))/Decimal("100"))*Decimal(str(direction))
            return float((Decimal(str(prev))*factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        zt_rows=[]; dt_rows=[]; zb_rows=[]
        for _, r in spot.iterrows():
            code=str(r.get("代码","")).zfill(6); name=str(r.get("名称", ""))
            price=_num(r.get("最新价")); prev=_num(r.get("昨收")); high=_num(r.get("最高")); lp=limit_pct(code,name)
            if price<=0 or prev<=0: continue
            up_px=_limit_price(prev,lp,1); dn_px=_limit_price(prev,lp,-1)
            tol=0.011
            if abs(price-up_px) <= tol:
                zt_rows.append(r)
            if abs(price-dn_px) <= tol:
                dt_rows.append(r)
            if high >= up_px-tol and price < up_px-tol:
                zb_rows.append(r)
        zt=pd.DataFrame(zt_rows); dtgc=pd.DataFrame(dt_rows); zbgc=pd.DataFrame(zb_rows)
        zt_count=len(zt); dt_count=len(dtgc); zb_count=len(zbgc)
        seal_rate=round(zt_count/max(1,zt_count+zb_count)*100,1)

        # Consecutive-limit estimate is expensive because it needs per-stock history.
        # On the dashboard fast path, publish the real all-A snapshot first and defer
        # this enrichment to on-demand review/stock-analysis endpoints.
        board_map: Dict[str,int] = {}
        board_errors=[]
        def board_work(row):
            code=str(row.get("代码","")).zfill(6); name=str(row.get("名称", "")); lp=limit_pct(code,name)
            try:
                hist, _src = fetch_history_df(code, 14)
                if hist.empty: return code,1
                h=hist.copy(); h["close"]=pd.to_numeric(h["close"],errors="coerce")
                closes=h["close"].dropna().tolist(); dates=h.get("date",pd.Series(dtype=str)).astype(str).tolist()
                consec=0
                # If provider does not yet include today's bar, current snapshot is board 1.
                includes_today=bool(dates and dates[-1][:10].replace('/','-') == now.strftime('%Y-%m-%d'))
                if not includes_today: consec=1
                for i in range(len(closes)-1,0,-1):
                    ret=(closes[i]/closes[i-1]-1)*100 if closes[i-1] else 0
                    if ret >= lp-0.45: consec += 1
                    else: break
                return code,max(1,consec)
            except Exception as exc:
                return code,1
        if not fast and not zt.empty:
            rows=zt.to_dict("records")[:36]
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs=[ex.submit(board_work,r) for r in rows]
                for fut in as_completed(futs):
                    try:
                        c,b=fut.result(); board_map[c]=b
                    except Exception as exc:
                        board_errors.append(type(exc).__name__)
        if fast and not zt.empty:
            board_map={str(r.get("代码","")).zfill(6):1 for _,r in zt.iterrows()}
        max_board=max(board_map.values(), default=(1 if zt_count else 0))

        ladder=[]
        if board_map:
            by=defaultdict(list)
            name_map={str(r.get("代码","")).zfill(6):str(r.get("名称","")) for _,r in zt.iterrows()}
            for c,b in board_map.items():
                if b>=2: by[b].append(name_map.get(c,c))
            for b in sorted(by, reverse=True):
                ladder.append({"board":int(b),"count":len(by[b]),"stocks":by[b][:5]})

        limitup_stocks=[]
        if not zt.empty:
            for _,r in zt.head(120).iterrows():
                code=str(r.get("代码","")).zfill(6)
                limitup_stocks.append({
                    "code":code,"name":str(r.get("名称","")),"pct":round(_num(r.get("涨跌幅")),2),
                    "industry":"","board":int(board_map.get(code,1)),"seal_amount":0.0,
                    "first_seal":"","last_seal":"","break_count":0,
                })

        # Candidate universe from the real snapshot. We do not invent industries when the source lacks them.
        base=spot.copy()
        base=base[~base["名称"].astype(str).str.upper().str.contains("ST|退",regex=True,na=False)]
        base=base[(base["成交额"].fillna(0)>=8e7) & (base["涨跌幅"].fillna(-99)>=0.8)]
        base["_rank"] = base["涨跌幅"].fillna(0)*2.1 + base["换手率"].fillna(0).clip(0,25)*0.32 + (base["成交额"].fillna(0)/1e9).clip(0,15)
        base=base.sort_values("_rank",ascending=False).head(90)

        active_stocks=[]
        for _,r in base.iterrows():
            active_stocks.append({
                "code":str(r.get("代码","")).zfill(6),"name":str(r.get("名称","")),"price":round(_num(r.get("最新价")),3),
                "pct":round(_num(r.get("涨跌幅")),2),"volume_ratio":round(_num(r.get("量比")),2),"turnover_rate":round(_num(r.get("换手率")),2),
                "amount":_num(r.get("成交额")),"high":_num(r.get("最高")),"low":_num(r.get("最低")),
                "open":_num(r.get("今开")),"prev_close":_num(r.get("昨收")),"industry":"",
                "market_cap":_num(r.get("总市值"))*10000,"float_market_cap":_num(r.get("流通市值"))*10000,
            })
        # Include limit-up stocks omitted by liquidity filter.
        seen={x["code"] for x in active_stocks}
        spot_map={str(r.get("代码","")).zfill(6):r for _,r in spot.iterrows()}
        for lu in limitup_stocks:
            if lu["code"] in seen: continue
            r=spot_map.get(lu["code"],{})
            active_stocks.append({
                "code":lu["code"],"name":lu["name"],"price":round(_num(getattr(r,'get',lambda *a:0)("最新价")),3),
                "pct":round(_num(getattr(r,'get',lambda *a:lu.get('pct',0))("涨跌幅")),2),"volume_ratio":round(_num(getattr(r,'get',lambda *a:0)("量比")),2),
                "turnover_rate":round(_num(getattr(r,'get',lambda *a:0)("换手率")),2),"amount":_num(getattr(r,'get',lambda *a:0)("成交额")),
                "high":_num(getattr(r,'get',lambda *a:0)("最高")),"low":_num(getattr(r,'get',lambda *a:0)("最低")),
                "open":_num(getattr(r,'get',lambda *a:0)("今开")),"prev_close":_num(getattr(r,'get',lambda *a:0)("昨收")),"industry":"",
            })

        # Enrich the most relevant names with a true 5-day volume ratio from daily K-lines.
        def vr_work(x):
            try:
                hist,_=fetch_history_df(x["code"], 12)
                if len(hist)<6:return x["code"],0.0
                v=pd.to_numeric(hist["volume"],errors="coerce").dropna()
                if len(v)<6:return x["code"],0.0
                prev5=float(v.iloc[-6:-1].mean()); return x["code"],(float(v.iloc[-1])/prev5 if prev5>0 else 0.0)
            except Exception:return x["code"],0.0
        vr_map={}
        # EastMoney full snapshot already carries real-time volume ratio.  Historical
        # K-line enrichment is only needed for missing values and must never delay the
        # fast dashboard path.
        missing_vr=[x for x in active_stocks[:24] if not x.get("volume_ratio")]
        if (not fast) and missing_vr:
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs=[ex.submit(vr_work,x) for x in missing_vr]
                for fut in as_completed(futs):
                    c,v=fut.result(); vr_map[c]=v
            for x in active_stocks:
                if x["code"] in vr_map: x["volume_ratio"]=round(vr_map[x["code"]],2)

        # Review universe for short-term research: do NOT rank by absolute turnover amount.
        # Favor moderate float cap, active turnover, tradable daily move and sufficient (not gigantic) liquidity.
        rv=spot.copy(); rv=rv[~rv["名称"].astype(str).str.upper().str.contains("ST|退",regex=True,na=False)]
        rv=rv[(rv["成交额"].fillna(0)>=8e7) & (rv["涨跌幅"].fillna(-99)>=-2.5) & (rv["涨跌幅"].fillna(99)<=9.5)]
        rv=rv[(rv["最新价"].fillna(0)>2.5) & (rv["最新价"].fillna(0)<=80)]
        if "流通市值" in rv.columns:
            cap_yi=rv["流通市值"].fillna(0)*10000/1e8
            rv=rv[(cap_yi<=0) | ((cap_yi>=10) & (cap_yi<=320))]
            cap_yi=rv["流通市值"].fillna(0)*10000/1e8
        else:
            cap_yi=pd.Series(0,index=rv.index,dtype=float)
        amt_yi=rv["成交额"].fillna(0)/1e8
        trv=rv["换手率"].fillna(0)
        pctv=rv["涨跌幅"].fillna(0)
        # bell-ish scores around practical ultra-short bands
        amt_s=(100-(amt_yi-10).abs()*4.8).clip(0,100)
        tr_s=(100-(trv-9).abs()*7.0).clip(0,100)
        pct_s=(100-(pctv-4).abs()*11.0).clip(0,100)
        cap_s=(100-(cap_yi-70).abs()*0.75).clip(0,100).where(cap_yi>0,55)
        rv["_rr"]=amt_s*.25 + tr_s*.35 + pct_s*.20 + cap_s*.20
        rv=rv.sort_values("_rr",ascending=False).head(280)
        review_universe=[]
        for _,r in rv.iterrows():
            review_universe.append({
                "code":str(r.get("代码","")).zfill(6),"name":str(r.get("名称","")),"price":round(_num(r.get("最新价")),3),
                "pct":round(_num(r.get("涨跌幅")),2),"volume_ratio":round(_num(r.get("量比")),2),"turnover_rate":round(_num(r.get("换手率")),2),
                "amount":_num(r.get("成交额")),"high":_num(r.get("最高")),"low":_num(r.get("最低")),
                "open":_num(r.get("今开")),"prev_close":_num(r.get("昨收")),"industry":"",
                "market_cap":_num(r.get("总市值"))*10000,"float_market_cap":_num(r.get("流通市值"))*10000,
            })

        quality = "full" if full_market else "partial"
        if not full_market:
            # Never present sample-pool counts as full-market facts.
            zt_count = dt_count = zb_count = None
            seal_rate = None
        source_errors=list(meta.get("errors") or [])
        if board_errors: source_errors.append("连板历史部分失败")
        notice = None if full_market else f"实时快照仅取得 {len(spot)} / {meta.get('expected') or '?'} 只，市场广度与涨跌停统计标记为不完整；个股候选仍使用真实已取得行情。"

        return {
            "source":f"{meta.get('provider') or '公开实时行情'} + 腾讯/新浪K线",
            "is_live":True,
            "source_errors":source_errors[:20],
            "source_status":{
                "market_snapshot":meta,
                "history":"腾讯财经→新浪财经K线回退",
                "quality":quality,
            },
            "data_quality":quality,
            "coverage":meta.get("coverage"),
            "fast_live":bool(fast),
            "enrichment_pending":bool(fast),
            "notice":((notice + " ") if notice else "") + ("首页已先返回真实全A快照；连板历史、量比和深度K线按需计算。" if fast else ""),
            "trade_date":datetime.strptime(date,"%Y%m%d").strftime("%Y-%m-%d"),
            "updated_at":now.isoformat(timespec="seconds"),
            "market_status":_market_status(now),
            "zt_count":int(zt_count),"zb_count":int(zb_count),"dt_count":int(dt_count),"seal_rate":seal_rate,
            "yesterday_premium":None,"yesterday_lianban_premium":None,
            "up_count":up_count,"down_count":down_count,"flat_count":flat_count,"turnover":turnover,
            "max_board":int(max_board),"promotion_rates":[],"ladder":ladder[:10],"themes":[],"concepts":[],
            "auction":{"avg_gap":None,"red_ratio":None,"strong_ratio":None,"leaders":[]},
            "active_stocks":active_stocks,"review_universe":review_universe,"limitup_stocks":limitup_stocks,
        }


def _market_status(now: datetime) -> str:
    if now.weekday() >= 5:
        return "休市"
    t = now.time()
    if time(9, 15) <= t < time(9, 25): return "集合竞价"
    if time(9, 25) <= t < time(9, 30): return "竞价结束"
    if time(9, 30) <= t <= time(11, 30) or time(13, 0) <= t <= time(15, 0): return "交易中"
    if time(11, 30) < t < time(13, 0): return "午间休市"
    return "已收盘" if t > time(15, 0) else "未开盘"


def get_provider(force_demo: bool = False):
    if force_demo:
        return DemoProvider()
    return DirectPublicProvider()
