from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
HTTP_TIMEOUT = float(os.getenv("PUBLIC_SOURCE_TIMEOUT", "4.5"))
SINA_WORKERS = int(os.getenv("SINA_WORKERS", "10"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
SINA_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://vip.stock.finance.sina.com.cn/",
    "Accept": "application/json,text/plain,*/*",
}
QQ_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://gu.qq.com/",
    "Accept": "application/json,text/plain,*/*",
}

SINA_LIST = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_COUNT = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount"
SINA_KLINE = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
QQ_KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
]

# 东方财富全A快照：优先使用 2026 年 AKShare 已切换的 push2delay 域名，
# 一次请求拿全市场，避免新浪 70+ 页分页导致慢和统计失真。
EASTMONEY_SPOT_HOSTS = [
    "https://82.push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
]
EASTMONEY_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://quote.eastmoney.com/center/gridlist.html#hs_a_board",
    "Accept": "application/json,text/plain,*/*",
}
EASTMONEY_TIMEOUT = float(os.getenv("EASTMONEY_TIMEOUT", "5.5"))

_hist_cache: Dict[str, Tuple[float, pd.DataFrame, str]] = {}
_hist_lock = threading.Lock()
HISTORY_CACHE_SECONDS = int(os.getenv("HISTORY_CACHE_SECONDS", "300"))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _loose_json(text: str) -> Any:
    text = (text or "").strip().lstrip("\ufeff")
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        pass
    # Sina has historically returned JavaScript object literals with unquoted keys.
    fixed = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text)
    fixed = fixed.replace("'", '"')
    try:
        return json.loads(fixed)
    except Exception:
        return []


def _get(url: str, *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> requests.Response:
    r = requests.get(url, params=params, headers=headers or {"User-Agent": UA}, timeout=timeout or HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def fetch_eastmoney_all_a() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Fast full-market A-share snapshot from EastMoney.

    Uses one large-page request instead of crawling ~70 Sina pages.  The result
    is only considered full-market when the returned row count is close to the
    provider's own total.  On failure callers can fall back to Sina.
    """
    started=time.time(); errors=[]
    params={
        "pn":"1", "pz":"8000", "po":"1", "np":"1",
        "ut":"bd1d9ddb04089700cf9c27f6f7426281", "fltt":"2", "invt":"2",
        "fid":"f3",
        "fs":"m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields":"f2,f3,f4,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21",
    }
    for url in EASTMONEY_SPOT_HOSTS:
        try:
            r=requests.get(url,params=params,headers=EASTMONEY_HEADERS,timeout=EASTMONEY_TIMEOUT)
            r.raise_for_status(); j=r.json(); data=(j or {}).get("data") or {}
            raw=data.get("diff") or []
            if isinstance(raw,dict): raw=list(raw.values())
            if not isinstance(raw,list) or not raw:
                errors.append(f"{url}:empty"); continue
            rows=[]
            for x in raw:
                if not isinstance(x,dict): continue
                cap=_f(x.get("f20")); fcap=_f(x.get("f21"))
                rows.append({
                    "code":str(x.get("f12") or "").zfill(6), "name":str(x.get("f14") or ""),
                    "trade":_f(x.get("f2")), "changepercent":_f(x.get("f3")),
                    "settlement":_f(x.get("f18")), "open":_f(x.get("f17")),
                    "high":_f(x.get("f15")), "low":_f(x.get("f16")),
                    "volume":_f(x.get("f5")), "amount":_f(x.get("f6")),
                    "turnoverratio":_f(x.get("f8")), "volume_ratio":_f(x.get("f10")),
                    # provider historically expects Sina's 10k-RMB cap unit and converts back to yuan
                    "mktcap":cap/10000 if cap else 0.0, "nmc":fcap/10000 if fcap else 0.0,
                })
            df=pd.DataFrame(rows)
            if not df.empty:
                df=df[df["code"].str.match(r"^\\d{6}$",na=False)].drop_duplicates("code",keep="last")
            expected=int(data.get("total") or len(df) or 0)
            coverage=(len(df)/expected) if expected else (1.0 if len(df)>=4500 else 0.0)
            meta={"provider":"东方财富全A直连","expected":expected,"rows":int(len(df)),
                  "coverage":round(min(1.0,coverage),3),
                  "full_market":bool((expected and len(df)>=expected*0.95) or (not expected and len(df)>=4500)),
                  "errors":errors[:8],"elapsed_ms":int((time.time()-started)*1000),"mode":"one-shot"}
            if len(df)>=1000:
                return df,meta
            errors.append(f"{url}:rows={len(df)}")
        except Exception as exc:
            errors.append(f"{url}:{type(exc).__name__}")
    raise RuntimeError("东方财富全A快照失败: "+" / ".join(errors[:6]))


def fetch_full_market_snapshot() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Full-market snapshot with fast provider first and full Sina fallback."""
    errors=[]
    try:
        return fetch_eastmoney_all_a()
    except Exception as exc:
        errors.append(f"eastmoney:{type(exc).__name__}")
    df,meta=fetch_sina_all_a()
    meta=dict(meta); meta["fallback_errors"]=errors
    return df,meta


def fetch_sina_count(node: str = "hs_a") -> int:
    r = _get(SINA_COUNT, params={"node": node}, headers=SINA_HEADERS)
    txt = r.text.strip().strip('"')
    try:
        return int(float(txt))
    except Exception:
        try:
            return int(r.json())
        except Exception:
            return 0


def fetch_sina_page(page: int, *, sort: str = "symbol", asc: int = 1, node: str = "hs_a", num: int = 80) -> List[Dict[str, Any]]:
    params = {
        "page": str(page), "num": str(num), "sort": sort, "asc": str(int(asc)),
        "node": node, "symbol": "", "_s_r_a": "page" if page > 1 else "init",
    }
    r = _get(SINA_LIST, params=params, headers=SINA_HEADERS)
    data = _loose_json(r.text)
    return data if isinstance(data, list) else []


def _fetch_pages(pages: List[int], *, sort: str, asc: int, workers: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        jobs = {ex.submit(fetch_sina_page, p, sort=sort, asc=asc): p for p in pages}
        for fut in as_completed(jobs):
            p = jobs[fut]
            try:
                part = fut.result()
                if part:
                    rows.extend(part)
                else:
                    errors.append(f"page{p}:empty")
            except Exception as exc:
                errors.append(f"page{p}:{type(exc).__name__}")
    return rows, errors


def fetch_sina_all_a() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Fetch the all-A snapshot directly from Sina, without AKShare.

    If a full symbol-sorted crawl is incomplete, merge several ranked pages as a partial
    fallback. The caller receives coverage metadata and must not pretend a partial sample
    is a full-market breadth reading.
    """
    started = time.time()
    errors: List[str] = []
    expected = 0
    try:
        expected = fetch_sina_count("hs_a")
    except Exception as exc:
        errors.append(f"count:{type(exc).__name__}")
    total_pages = min(85, max(1, math.ceil(expected / 80))) if expected else 70
    rows, page_errors = _fetch_pages(list(range(1, total_pages + 1)), sort="symbol", asc=1, workers=SINA_WORKERS)
    errors.extend(page_errors[:16])

    def dedup(items: List[Dict[str, Any]]) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        df = pd.DataFrame(items)
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.zfill(6)
            df = df.drop_duplicates("code", keep="last")
        return df

    df = dedup(rows)
    coverage = (len(df) / expected) if expected else (1.0 if len(df) >= 4000 else 0.0)

    # Full crawl unavailable: collect ranked pages to keep candidates usable.
    if len(df) < 1200 or coverage < 0.55:
        ranked_rows: List[Dict[str, Any]] = []
        ranked_jobs = [
            ("amount", 0, range(1, 7)),
            ("changepercent", 0, range(1, 5)),
            ("changepercent", 1, range(1, 5)),
            ("turnoverratio", 0, range(1, 4)),
        ]
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(_fetch_pages, list(pages), sort=sort, asc=asc, workers=4):(sort,asc) for sort,asc,pages in ranked_jobs}
            for fut in as_completed(futs):
                try:
                    part, errs = fut.result(); ranked_rows.extend(part); errors.extend(errs[:4])
                except Exception as exc:
                    errors.append(f"ranked:{type(exc).__name__}")
        df = dedup(rows + ranked_rows)
        coverage = (len(df) / expected) if expected else 0.0

    meta = {
        "provider": "新浪财经直连",
        "expected": expected,
        "rows": int(len(df)),
        "coverage": round(min(1.0, coverage), 3),
        "full_market": bool(expected and len(df) >= expected * 0.80) or (not expected and len(df) >= 4000),
        "errors": errors[:20],
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    return df, meta



def fetch_sina_fast_snapshot() -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Fast partial real snapshot for dashboard/selection.

    Avoids a 70-page full-market crawl on every refresh. It merges several
    ranked slices (amount/change/turnover) plus a few symbol pages, which is
    enough for hot-stock screening while being explicitly marked partial.
    """
    started=time.time(); errors=[]; expected=0
    try:
        expected=fetch_sina_count("hs_a")
    except Exception as exc:
        errors.append(f"count:{type(exc).__name__}")
    jobs=[
        ("amount",0,list(range(1,9))),
        ("changepercent",0,list(range(1,7))),
        ("turnoverratio",0,list(range(1,6))),
        ("changepercent",1,list(range(1,4))),
        ("symbol",1,list(range(1,5))),
    ]
    rows=[]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs={ex.submit(_fetch_pages,pages,sort=sort,asc=asc,workers=5):(sort,asc) for sort,asc,pages in jobs}
        for fut in as_completed(futs):
            try:
                part,errs=fut.result(); rows.extend(part); errors.extend(errs[:4])
            except Exception as exc:
                errors.append(f"fast:{type(exc).__name__}")
    if rows:
        df=pd.DataFrame(rows)
        if "code" in df.columns:
            df["code"]=df["code"].astype(str).str.zfill(6)
            df=df.drop_duplicates("code",keep="last")
    else:
        df=pd.DataFrame()
    coverage=(len(df)/expected) if expected else 0.0
    return df,{
        "provider":"新浪财经直连-快速池","expected":expected,"rows":int(len(df)),
        "coverage":round(min(1.0,coverage),3),"full_market":False,
        "errors":errors[:20],"elapsed_ms":int((time.time()-started)*1000),
        "mode":"fast-partial",
    }

def fetch_tencent_quotes(codes: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Batch real-time quotes for a small watchlist (paper trading).

    Uses Tencent qt.gtimg.cn and intentionally avoids an all-market crawl.
    """
    started=time.time(); codes=[str(c).zfill(6) for c in codes if str(c).strip()]
    codes=list(dict.fromkeys(codes))[:200]
    if not codes:
        return [],{"provider":"腾讯财经实时","rows":0,"elapsed_ms":0,"errors":[]}
    syms=[tencent_symbol(c) for c in codes]
    url="https://qt.gtimg.cn/q="+",".join(syms)
    errors=[]; out=[]
    try:
        r=_get(url,headers=QQ_HEADERS,timeout=min(5.0,HTTP_TIMEOUT))
        r.encoding="gbk"
        text=r.text or ""
        for line in text.splitlines():
            if '="' not in line: continue
            try:
                payload=line.split('="',1)[1].rsplit('"',1)[0]
                f=payload.split('~')
                if len(f)<35: continue
                code=str(f[2]).zfill(6); price=_f(f[3]); prev=_f(f[4]); op=_f(f[5]);
                if price<=0: continue
                pct=_f(f[32], ((price/prev-1)*100 if prev else 0.0))
                high=_f(f[33],price); low=_f(f[34],price)
                amount=_f(f[37]) if len(f)>37 else _f(f[7])*10000
                turnover=_f(f[38]) if len(f)>38 else 0.0
                volume_ratio=_f(f[49]) if len(f)>49 else 0.0
                out.append({
                    "code":code,"name":f[1],"price":price,"pct":pct,"open":op,
                    "prev_close":prev,"high":high,"low":low,"amount":amount,
                    "turnover_rate":turnover,"volume_ratio":volume_ratio,"industry":"",
                })
            except Exception as exc:
                errors.append(type(exc).__name__)
    except Exception as exc:
        errors.append(f"request:{type(exc).__name__}:{exc}")
    return out,{"provider":"腾讯财经实时","rows":len(out),"elapsed_ms":int((time.time()-started)*1000),"errors":errors[:10]}

def tencent_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def _parse_qq_payload(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if "=" in text and not text.lstrip().startswith("{"):
        text = text.split("=", 1)[1].strip().rstrip(";")
    return json.loads(text)


def _history_from_tencent(code: str, count: int) -> Tuple[pd.DataFrame, str]:
    sym = tencent_symbol(code)
    last_err = None
    for host in QQ_KLINE_HOSTS:
        try:
            r = _get(host, params={"param": f"{sym},day,,,{int(count)},qfq"}, headers=QQ_HEADERS)
            obj = _parse_qq_payload(r.text)
            node = ((obj.get("data") or {}).get(sym) or {}) if isinstance(obj, dict) else {}
            raw = node.get("qfqday") or node.get("day") or []
            if not raw:
                last_err = "empty"; continue
            rows = []
            for x in raw:
                if not isinstance(x, (list, tuple)) or len(x) < 6:
                    continue
                rows.append({
                    "date": str(x[0]), "open": _f(x[1]), "close": _f(x[2]),
                    "high": _f(x[3]), "low": _f(x[4]), "volume": _f(x[5]),
                    "amount": _f(x[6]) if len(x) > 6 else 0.0,
                })
            df = pd.DataFrame(rows)
            if len(df) >= min(10, count // 3):
                return df, "腾讯财经"
        except Exception as exc:
            last_err = type(exc).__name__
    raise RuntimeError(f"Tencent history unavailable: {last_err}")


def _history_from_sina(code: str, count: int) -> Tuple[pd.DataFrame, str]:
    sym = tencent_symbol(code)
    # Sina kline endpoint accepts sh/sz symbols. BSE support is inconsistent; fail cleanly.
    if sym.startswith("bj"):
        raise RuntimeError("Sina BSE kline unsupported")
    r = _get(SINA_KLINE, params={"symbol": sym, "scale": "240", "ma": "5", "datalen": str(int(count))}, headers=SINA_HEADERS)
    data = _loose_json(r.text)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Sina history empty")
    rows=[]
    for x in data:
        if not isinstance(x, dict): continue
        rows.append({
            "date": str(x.get("day") or x.get("date") or ""),
            "open": _f(x.get("open")), "close": _f(x.get("close")),
            "high": _f(x.get("high")), "low": _f(x.get("low")),
            "volume": _f(x.get("volume")), "amount": _f(x.get("amount")),
        })
    df=pd.DataFrame(rows)
    if df.empty: raise RuntimeError("Sina history parse empty")
    return df, "新浪财经K线"


def fetch_history_df(code: str, count: int = 120, *, use_cache: bool = True) -> Tuple[pd.DataFrame, str]:
    code = str(code).zfill(6)
    key = code
    now = time.time()
    if use_cache:
        with _hist_lock:
            item = _hist_cache.get(key)
            if item and now - item[0] < HISTORY_CACHE_SECONDS and len(item[1]) >= min(count, 60):
                return item[1].tail(count).copy(), item[2]
    errors=[]
    for fn in (_history_from_tencent, _history_from_sina):
        try:
            df, source = fn(code, max(count, 120))
            if not df.empty:
                with _hist_lock:
                    _hist_cache[key] = (now, df.copy(), source)
                return df.tail(count).copy(), source
        except Exception as exc:
            errors.append(f"{fn.__name__}:{type(exc).__name__}")
    raise RuntimeError(" / ".join(errors) or "history unavailable")


def limit_pct(code: str, name: str = "") -> float:
    code = str(code).zfill(6)
    upname = str(name).upper()
    if "ST" in upname:
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def probe_sources() -> Dict[str, Any]:
    """Fast diagnostic endpoint; each probe has a hard HTTP timeout."""
    def one(name, fn):
        st=time.time()
        try:
            value=fn()
            return name, {"ok": True, "ms": int((time.time()-st)*1000), "detail": value}
        except Exception as exc:
            return name, {"ok": False, "ms": int((time.time()-st)*1000), "error": f"{type(exc).__name__}: {exc}"}

    tests = [
        ("eastmoney_full", lambda: {"rows": len(fetch_eastmoney_all_a()[0]), "meta": fetch_eastmoney_all_a()[1]}),
        ("sina_list", lambda: len(fetch_sina_page(1, sort="amount", asc=0))),
        ("sina_count", lambda: fetch_sina_count("hs_a")),
        ("tencent_kline", lambda: len(fetch_history_df("600519", 20, use_cache=False)[0])),
    ]
    out={}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs=[ex.submit(one,n,fn) for n,fn in tests]
        for fut in as_completed(futs):
            k,v=fut.result(); out[k]=v
    return out
