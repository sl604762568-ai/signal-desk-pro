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

    def fetch(self) -> Dict[str, Any]:
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


class AKShareProvider:
    name = "AKShare / 东方财富"

    def __init__(self):
        import akshare as ak  # type: ignore
        self.ak = ak

    def _latest_trade_date(self) -> str:
        now = datetime.now(CN_TZ)
        try:
            cal = self.ak.tool_trade_date_hist_sina()
            col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
            dates = pd.to_datetime(cal[col]).dt.date
            candidates = [d for d in dates if d <= now.date()]
            if candidates:
                return max(candidates).strftime("%Y%m%d")
        except Exception:
            pass
        # 兜底：周末向前推，节假日由接口报错后上层回退。
        d = now.date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y%m%d")

    def fetch(self) -> Dict[str, Any]:
        ak = self.ak
        now = datetime.now(CN_TZ)
        date = self._latest_trade_date()

        errors: List[str] = []
        def safe_df(label, fn):
            try:
                df = fn()
                return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
            except Exception as exc:
                errors.append(f"{label}:{type(exc).__name__}")
                return pd.DataFrame()

        # 单个源失败不再拖垮整个工作台。
        zt = safe_df("涨停池", lambda: ak.stock_zt_pool_em(date=date))
        zbgc = safe_df("炸板池", lambda: ak.stock_zt_pool_zbgc_em(date=date))
        dtgc = safe_df("跌停池", lambda: ak.stock_zt_pool_dtgc_em(date=date))
        prev = safe_df("昨日涨停", lambda: ak.stock_zt_pool_previous_em(date=date))
        concept = safe_df("概念板块", lambda: ak.stock_board_concept_name_em())

        spot_source = "东方财富"
        spot = safe_df("全A东财", lambda: ak.stock_zh_a_spot_em())
        # 东财在部分云机房可能超时，改用新浪全A做第二路回退。新浪字段已经被 AKShare 标准化。
        if spot.empty or "代码" not in spot.columns or "涨跌幅" not in spot.columns:
            spot_source = "新浪"
            spot = safe_df("全A新浪", lambda: ak.stock_zh_a_spot())
        if spot.empty:
            raise RuntimeError("全A实时行情两路均不可用：" + ",".join(errors[-4:]))

        zt_count = int(len(zt))
        zb_count = int(len(zbgc))
        dt_count = int(len(dtgc))
        seal_rate = round(zt_count / max(1, zt_count + zb_count) * 100, 1)
        yesterday_premium = round(_safe_mean(prev.get("涨跌幅", [])), 2)
        if not prev.empty and "昨日连板数" in prev.columns:
            lprev = prev[pd.to_numeric(prev["昨日连板数"], errors="coerce").fillna(0) >= 2]
            yesterday_lianban_premium = round(_safe_mean(lprev.get("涨跌幅", [])), 2)
        else:
            yesterday_lianban_premium = 0.0

        pct = pd.to_numeric(spot.get("涨跌幅"), errors="coerce")
        up_count = int((pct > 0).sum())
        down_count = int((pct < 0).sum())
        flat_count = int((pct == 0).sum())
        turnover = float(pd.to_numeric(spot.get("成交额"), errors="coerce").fillna(0).sum())

        board_ser = pd.to_numeric(zt.get("连板数"), errors="coerce").fillna(1).astype(int) if not zt.empty else pd.Series(dtype=int)
        max_board = int(board_ser.max()) if not board_ser.empty else 0

        # 连板梯队
        ladder: List[Dict[str, Any]] = []
        if not zt.empty and "连板数" in zt.columns:
            zt2 = zt.copy()
            zt2["_board"] = pd.to_numeric(zt2["连板数"], errors="coerce").fillna(1).astype(int)
            for b in sorted([x for x in zt2["_board"].unique().tolist() if x >= 2], reverse=True):
                rows = zt2[zt2["_board"] == b]
                ladder.append({"board": int(b), "count": int(len(rows)), "stocks": rows["名称"].astype(str).head(5).tolist()})

        # 晋级率：昨日 N 板作为分母，今日 N+1 板作为分子。
        promotions: List[Dict[str, Any]] = []
        prev_board = pd.to_numeric(prev.get("昨日连板数"), errors="coerce").fillna(0).astype(int) if not prev.empty and "昨日连板数" in prev.columns else pd.Series(dtype=int)
        today_board = board_ser
        for n in range(1, max(5, max_board)):
            denominator = int((prev_board == n).sum())
            numerator = int((today_board == n + 1).sum())
            rate = round(numerator / denominator * 100, 1) if denominator else 0.0
            promotions.append({"label": f"{n}→{n+1}", "numerator": numerator, "denominator": denominator, "rate": rate})

        # 涨停股按“所属行业”聚合，作为题材/行业线索。
        themes: List[Dict[str, Any]] = []
        if not zt.empty and "所属行业" in zt.columns:
            tmp = zt.copy()
            tmp["_board"] = pd.to_numeric(tmp.get("连板数"), errors="coerce").fillna(1).astype(int)
            grouped = []
            for name, rows in tmp.groupby("所属行业"):
                if not name or str(name) == "nan":
                    continue
                cnt = len(rows)
                mb = int(rows["_board"].max())
                score = min(100, round(cnt * 9 + max(0, mb - 1) * 10, 1))
                leaders = rows.sort_values(["_board", "涨跌幅"], ascending=[False, False])["名称"].astype(str).head(3).tolist()
                grouped.append({"name": str(name), "limitups": int(cnt), "max_board": mb, "score": score, "leaders": leaders})
            themes = sorted(grouped, key=lambda x: (x["score"], x["limitups"]), reverse=True)[:10]

        # 概念实时排行：不逐个拉成份股，避免接口过载。
        concepts: List[Dict[str, Any]] = []
        if not concept.empty:
            c = concept.copy()
            c["_pct"] = pd.to_numeric(c.get("涨跌幅"), errors="coerce").fillna(-999)
            c = c.sort_values("_pct", ascending=False).head(10)
            for _, r in c.iterrows():
                concepts.append({
                    "name": str(r.get("板块名称", "")),
                    "pct": round(_num(r.get("涨跌幅")), 2),
                    "up": int(_num(r.get("上涨家数"))),
                    "down": int(_num(r.get("下跌家数"))),
                    "leader": str(r.get("领涨股票", "")),
                    "leader_pct": round(_num(r.get("领涨股票-涨跌幅")), 2),
                })

        # 昨日涨停竞价：用全A实时表的今开/昨收合并计算。
        auction = {"avg_gap": None, "red_ratio": None, "strong_ratio": None, "leaders": []}
        if not prev.empty and not spot.empty and "代码" in prev.columns and "代码" in spot.columns:
            p = prev[["代码", "名称"]].drop_duplicates().copy()
            s = spot[["代码", "今开", "昨收"]].copy()
            p["代码"] = p["代码"].astype(str).str.zfill(6)
            s["代码"] = s["代码"].astype(str).str.zfill(6)
            m = p.merge(s, on="代码", how="left")
            m["今开"] = pd.to_numeric(m["今开"], errors="coerce")
            m["昨收"] = pd.to_numeric(m["昨收"], errors="coerce")
            m = m[(m["今开"] > 0) & (m["昨收"] > 0)].copy()
            if not m.empty:
                m["gap"] = (m["今开"] / m["昨收"] - 1) * 100
                auction = {
                    "avg_gap": round(float(m["gap"].mean()), 2),
                    "red_ratio": round(float((m["gap"] > 0).mean() * 100), 1),
                    "strong_ratio": round(float((m["gap"] >= 3).mean() * 100), 1),
                    "leaders": [
                        {"code": str(r["代码"]), "name": str(r["名称"]), "gap": round(float(r["gap"]), 2)}
                        for _, r in m.sort_values("gap", ascending=False).head(8).iterrows()
                    ],
                }

        # 活跃候选池：实时全A中筛选有成交、非ST、强势且具流动性的股票。
        active_stocks: List[Dict[str, Any]] = []
        if not spot.empty and "代码" in spot.columns:
            sp = spot.copy()
            for col in ["涨跌幅","量比","换手率","成交额","最新价","最高","最低","今开","昨收"]:
                if col in sp.columns:
                    sp[col] = pd.to_numeric(sp[col], errors="coerce")
            if "名称" in sp.columns:
                sp = sp[~sp["名称"].astype(str).str.upper().str.contains("ST", na=False)]
            if "成交额" in sp.columns and "涨跌幅" in sp.columns:
                sp = sp[(sp["成交额"].fillna(0) >= 8e7) & (sp["涨跌幅"].fillna(-99) >= 1.0) & (sp["涨跌幅"].fillna(99) <= 10.5)]
                vr = sp["量比"].fillna(0) if "量比" in sp.columns else 0
                tr = sp["换手率"].fillna(0) if "换手率" in sp.columns else 0
                sp["_rank"] = sp["涨跌幅"].fillna(0)*2.2 + pd.Series(vr, index=sp.index).clip(0,5)*2 + pd.Series(tr,index=sp.index).clip(0,20)*0.35 + (sp["成交额"].fillna(0)/1e9).clip(0,10)
                sp = sp.sort_values("_rank", ascending=False).head(60)
            # 行业先用涨停池补齐；普通活跃股允许为空。
            ind_map = {}
            if not zt.empty and "代码" in zt.columns and "所属行业" in zt.columns:
                ind_map = {str(r.get("代码","")).zfill(6): str(r.get("所属行业", "")) for _, r in zt.iterrows()}
            for _, r in sp.iterrows():
                code = str(r.get("代码", "")).zfill(6)
                active_stocks.append({
                    "code": code, "name": str(r.get("名称", "")), "price": round(_num(r.get("最新价")), 3),
                    "pct": round(_num(r.get("涨跌幅")), 2), "volume_ratio": round(_num(r.get("量比")), 2),
                    "turnover_rate": round(_num(r.get("换手率")), 2), "amount": _num(r.get("成交额")),
                    "high": _num(r.get("最高")), "low": _num(r.get("最低")), "open": _num(r.get("今开")),
                    "prev_close": _num(r.get("昨收")), "industry": ind_map.get(code, ""),
                })

        # 复盘选股使用更宽的流动性股票池：不要求当日上涨，避免漏掉缩量回踩/缠论二买。
        review_universe: List[Dict[str, Any]] = []
        if not spot.empty and "代码" in spot.columns:
            rv = spot.copy()
            for col in ["涨跌幅","量比","换手率","成交额","最新价","最高","最低","今开","昨收"]:
                if col in rv.columns:
                    rv[col] = pd.to_numeric(rv[col], errors="coerce")
            if "名称" in rv.columns:
                rv = rv[~rv["名称"].astype(str).str.upper().str.contains("ST", na=False)]
            if "成交额" in rv.columns and "涨跌幅" in rv.columns:
                rv = rv[(rv["成交额"].fillna(0) >= 1.2e8) & (rv["涨跌幅"].fillna(-99) >= -6.0) & (rv["涨跌幅"].fillna(99) <= 10.5)]
                # 先按流动性+活跃度压缩到可承受的历史K请求规模。
                rv["_rr"] = (rv["成交额"].fillna(0)/1e9).clip(0,20) * 1.8 + rv["涨跌幅"].abs().fillna(0) * .35
                rv = rv.sort_values("_rr", ascending=False).head(180)
            ind_map2 = {}
            if not zt.empty and "代码" in zt.columns and "所属行业" in zt.columns:
                ind_map2 = {str(r.get("代码","")).zfill(6): str(r.get("所属行业", "")) for _, r in zt.iterrows()}
            for _, r in rv.iterrows():
                code = str(r.get("代码", "")).zfill(6)
                review_universe.append({
                    "code": code, "name": str(r.get("名称", "")), "price": round(_num(r.get("最新价")), 3),
                    "pct": round(_num(r.get("涨跌幅")), 2), "volume_ratio": round(_num(r.get("量比")), 2),
                    "turnover_rate": round(_num(r.get("换手率")), 2), "amount": _num(r.get("成交额")),
                    "high": _num(r.get("最高")), "low": _num(r.get("最低")), "open": _num(r.get("今开")),
                    "prev_close": _num(r.get("昨收")), "industry": ind_map2.get(code, ""),
                })

        limitup_stocks: List[Dict[str, Any]] = []
        if not zt.empty:
            for _, r in zt.head(100).iterrows():
                limitup_stocks.append({
                    "code": str(r.get("代码", "")).zfill(6), "name": str(r.get("名称", "")),
                    "pct": round(_num(r.get("涨跌幅")),2), "industry": str(r.get("所属行业", "")),
                    "board": int(_num(r.get("连板数"),1)), "seal_amount": _num(r.get("封板资金")),
                    "first_seal": str(r.get("首次封板时间", "")), "last_seal": str(r.get("最后封板时间", "")),
                    "break_count": int(_num(r.get("炸板次数"))),
                })

        # 把涨停股并入活跃池，避免一字/低换手龙头被预筛遗漏。
        seen_codes = {x["code"] for x in active_stocks}
        spot_map = {str(r.get("代码","")).zfill(6): r for _, r in spot.iterrows()} if not spot.empty and "代码" in spot.columns else {}
        for lu in limitup_stocks:
            if lu["code"] in seen_codes:
                # 补行业
                for x in active_stocks:
                    if x["code"] == lu["code"] and not x.get("industry"):
                        x["industry"] = lu.get("industry", "")
                continue
            r = spot_map.get(lu["code"], {})
            active_stocks.append({
                "code":lu["code"],"name":lu["name"],"price":round(_num(getattr(r,'get',lambda *a:0)("最新价")),3),
                "pct":round(_num(getattr(r,'get',lambda *a:lu.get('pct',0))("涨跌幅")),2),
                "volume_ratio":round(_num(getattr(r,'get',lambda *a:0)("量比")),2),
                "turnover_rate":round(_num(getattr(r,'get',lambda *a:0)("换手率")),2),
                "amount":_num(getattr(r,'get',lambda *a:0)("成交额")), "high":_num(getattr(r,'get',lambda *a:0)("最高")),
                "low":_num(getattr(r,'get',lambda *a:0)("最低")),"open":_num(getattr(r,'get',lambda *a:0)("今开")),
                "prev_close":_num(getattr(r,'get',lambda *a:0)("昨收")),"industry":lu.get("industry", ""),
            })

        return {
            "source": f"AKShare / {spot_source}",
            "is_live": True,
            "source_errors": errors,
            "trade_date": datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d"),
            "updated_at": now.isoformat(timespec="seconds"),
            "market_status": _market_status(now),
            "zt_count": zt_count,
            "zb_count": zb_count,
            "dt_count": dt_count,
            "seal_rate": seal_rate,
            "yesterday_premium": yesterday_premium,
            "yesterday_lianban_premium": yesterday_lianban_premium,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "turnover": turnover,
            "max_board": max_board,
            "promotion_rates": promotions,
            "ladder": ladder[:10],
            "themes": themes,
            "concepts": concepts,
            "auction": auction,
            "active_stocks": active_stocks,
            "review_universe": review_universe,
            "limitup_stocks": limitup_stocks,
        }


def _market_status(now: datetime) -> str:
    if now.weekday() >= 5:
        return "休市"
    t = now.time()
    if time(9, 15) <= t < time(9, 25):
        return "集合竞价"
    if time(9, 25) <= t < time(9, 30):
        return "竞价结束"
    if time(9, 30) <= t <= time(11, 30) or time(13, 0) <= t <= time(15, 0):
        return "交易中"
    if time(11, 30) < t < time(13, 0):
        return "午间休市"
    return "已收盘" if t > time(15, 0) else "未开盘"


def get_provider(force_demo: bool = False):
    if force_demo:
        return DemoProvider()
    try:
        return AKShareProvider()
    except Exception:
        return DemoProvider()
