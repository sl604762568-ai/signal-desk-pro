from __future__ import annotations

import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
EM_CLIST_HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://82.push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://73.push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://29.push2.eastmoney.com/api/qt/clist/get",
]
EM_STOCK = "https://push2.eastmoney.com/api/qt/stock/get"
EM_SLIST = "https://push2.eastmoney.com/api/qt/slist/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TIMEOUT = 2.5
CACHE_SECONDS = 180

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Any]] = {}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "-":
            return default
        return float(v)
    except Exception:
        return default


def _get_json(url: str, params: Dict[str, Any], timeout: int = TIMEOUT) -> Dict[str, Any]:
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _clist_json(params: Dict[str, Any]) -> Dict[str, Any]:
    last = None
    for host in EM_CLIST_HOSTS:
        try:
            obj = _get_json(host, params)
            if obj and obj.get("data") is not None:
                return obj
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Eastmoney clist unavailable: {type(last).__name__ if last else 'unknown'}")


def _cached(key: str, ttl: int = CACHE_SECONDS):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.time() - item[0] < ttl:
            return item[1]
    return None


def _put(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.time(), value)


def _pct_rank(values: List[float], x: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 50.0
    count = sum(1 for v in clean if v <= x)
    return 100.0 * count / len(clean)


def _board_fs(kind: str) -> str:
    if kind == "industry":
        return "m:90+t:2+f:!50"
    return "m:90+t:3+f:!50"


def fetch_board_list(kind: str = "concept", limit: int = 500) -> List[Dict[str, Any]]:
    key = f"boards:{kind}:{limit}"
    c = _cached(key)
    if c is not None:
        return c
    base = {
        "pz": 100, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": _board_fs(kind),
        "fields": "f2,f3,f6,f8,f12,f14,f62,f66,f72,f78,f84,f104,f105,f106,f128,f136,f184,f204,f205,f164,f165,f174,f175,f109,f160",
    }
    first = _clist_json({**base, "pn": 1})
    data=(first or {}).get("data") or {}
    rows=list(data.get("diff") or [])
    total=int(_f(data.get("total"),len(rows)))
    pages=min(math.ceil(total/100), max(1,math.ceil(limit/100)))
    if pages>1:
        with ThreadPoolExecutor(max_workers=min(8,pages-1)) as ex:
            futs=[ex.submit(_clist_json,{**base,"pn":p}) for p in range(2,pages+1)]
            for fut in as_completed(futs):
                try:
                    rows.extend(list(((fut.result() or {}).get("data") or {}).get("diff") or []))
                except Exception:
                    pass
    out: List[Dict[str, Any]] = []
    seen=set()
    for r in rows:
        code = str(r.get("f12") or "")
        name = str(r.get("f14") or "")
        if not code.startswith("BK") or not name or code in seen:
            continue
        seen.add(code)
        up, down, flat = int(_f(r.get("f104"))), int(_f(r.get("f105"))), int(_f(r.get("f106")))
        total_n = up + down + flat
        out.append({
            "code": code, "name": name, "type": kind,
            "index": _f(r.get("f2")), "pct": _f(r.get("f3")), "amount": _f(r.get("f6")),
            "turnover": _f(r.get("f8")), "main_net": _f(r.get("f62")), "main_net_pct": _f(r.get("f184")),
            "super_net": _f(r.get("f66")), "large_net": _f(r.get("f72")), "mid_net": _f(r.get("f78")), "small_net": _f(r.get("f84")),
            "up": up, "down": down, "flat": flat, "breadth": (up / total_n * 100 if total_n else 50.0),
            "main_net_5d": (None if r.get("f164") in (None,"-") else _f(r.get("f164"))),
            "main_net_10d": (None if r.get("f174") in (None,"-") else _f(r.get("f174"))),
            "pct_5d": (None if r.get("f109") in (None,"-") else _f(r.get("f109"))),
            "pct_10d": (None if r.get("f160") in (None,"-") else _f(r.get("f160"))),
            "leader_name": str(r.get("f204") or r.get("f128") or ""),
            "leader_code": str(r.get("f205") or ""),
        })
    _put(key, out[:limit])
    return out[:limit]


def _heat_boards(boards: List[Dict[str, Any]], news: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if not boards:
        return []
    pcts = [b["pct"] for b in boards]
    breadths = [b["breadth"] for b in boards]
    flowp = [b["main_net_pct"] for b in boards]
    flows = [math.copysign(math.log10(abs(b["main_net"]) + 1), b["main_net"]) for b in boards]
    turns = [b["turnover"] for b in boards]
    items = (news or {}).get("items") or []
    clusters = (news or {}).get("clusters") or []

    out = []
    for b in boards:
        flow_log = math.copysign(math.log10(abs(b["main_net"]) + 1), b["main_net"])
        base = (
            _pct_rank(pcts, b["pct"]) * .34 +
            _pct_rank(breadths, b["breadth"]) * .24 +
            _pct_rank(flowp, b["main_net_pct"]) * .18 +
            _pct_rank(flows, flow_log) * .14 +
            _pct_rank(turns, b["turnover"]) * .10
        )
        news_heat = 0.0
        hits = 0
        bn = b["name"]
        for c in clusters[:24]:
            topic = str(c.get("topic") or "")
            if topic and (topic in bn or bn in topic):
                news_heat = max(news_heat, _f(c.get("heat"), 0))
        for it in items[:160]:
            title = str(it.get("title") or "")
            if bn and bn in title:
                hits += 1
                news_heat = max(news_heat, _f(it.get("heat"), 0))
        heat = min(100.0, base * .9 + min(10.0, news_heat / 10 + hits * 1.5))
        z = dict(b)
        z.update({
            "heat": round(heat, 1),
            "heat_components": {
                "price_rank": round(_pct_rank(pcts, b["pct"]), 1),
                "breadth_rank": round(_pct_rank(breadths, b["breadth"]), 1),
                "flow_rank": round(_pct_rank(flowp, b["main_net_pct"]), 1),
                "news_heat": round(news_heat, 1),
            },
        })
        out.append(z)
    out.sort(key=lambda x: (x["heat"], x["pct"], x["main_net_pct"]), reverse=True)
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out


def get_sector_heat(news: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    # Concept and industry lists are independent network calls; request them concurrently.
    concepts: List[Dict[str, Any]] = []
    industries: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fetch_board_list, "concept", 500)
        f2 = ex.submit(fetch_board_list, "industry", 500)
        try: concepts = f1.result()
        except Exception: concepts = []
        try: industries = f2.result()
        except Exception: industries = []
    if not concepts and not industries:
        raise RuntimeError("行业/概念板块数据源均不可用")
    # Deduplicate exact names by preferring concept when both exist, but preserve true code relation.
    seen = set(); merged = []
    for b in concepts + industries:
        name = b["name"]
        if name in seen: continue
        seen.add(name); merged.append(b)
    return _heat_boards(merged, news)


def fetch_board_members(board_code: str, limit: int = 500) -> List[Dict[str, Any]]:
    board_code = str(board_code).upper().strip()
    key = f"members:{board_code}"
    c = _cached(key)
    if c is not None:
        return c
    base = {
        "pz": 100, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2,
        "fid": "f3", "fs": f"b:{board_code}",
        "fields": "f2,f3,f6,f8,f10,f12,f14,f15,f16,f17,f18,f20,f21,f24,f25,f62",
    }
    first = _clist_json({**base, "pn": 1})
    data = (first or {}).get("data") or {}
    rows = list(data.get("diff") or [])
    total = int(_f(data.get("total"), len(rows)))
    pages = min(math.ceil(total / 100), max(1, math.ceil(limit / 100)))
    if pages > 1:
        with ThreadPoolExecutor(max_workers=min(6, pages - 1)) as ex:
            futs = [ex.submit(_clist_json, {**base, "pn": p}) for p in range(2, pages + 1)]
            for fut in as_completed(futs):
                try:
                    d = (fut.result() or {}).get("data") or {}
                    rows.extend(list(d.get("diff") or []))
                except Exception:
                    pass
    out = []
    seen=set()
    for r in rows:
        code = str(r.get("f12") or "").zfill(6)
        name = str(r.get("f14") or "")
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code, "name": name, "price": _f(r.get("f2")), "pct": _f(r.get("f3")),
            "amount": _f(r.get("f6")), "turnover_rate": _f(r.get("f8")), "volume_ratio": _f(r.get("f10")),
            "high": _f(r.get("f15")), "low": _f(r.get("f16")), "open": _f(r.get("f17")), "prev_close": _f(r.get("f18")),
            "market_cap": _f(r.get("f20")), "float_market_cap": _f(r.get("f21")),
            "pct60": _f(r.get("f24")), "pct_year": _f(r.get("f25")), "main_net": _f(r.get("f62")),
            "relation_source": "东方财富板块成分关系",
        })
    out.sort(key=lambda x: (x["pct"], x["amount"]), reverse=True)
    _put(key, out[:limit])
    return out[:limit]


def _market_id(code: str) -> str:
    code = str(code).zfill(6)
    return "1" if code.startswith(("6", "9")) else "0"


def fetch_stock_sector_info(code: str) -> Dict[str, Any]:
    code = str(code).zfill(6)
    key = f"stock_sector:{code}"
    c = _cached(key, 600)
    if c is not None:
        return c
    secid = f"{_market_id(code)}.{code}"
    result = {"code": code, "name": "", "industry": "", "concepts": [], "source": "东方财富个股行业/概念关系"}
    try:
        obj = _get_json(EM_STOCK, {
            "secid": secid, "fields": "f57,f58,f127,f116,f117",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        })
        d = (obj or {}).get("data") or {}
        result["name"] = str(d.get("f58") or "")
        result["industry"] = str(d.get("f127") or "")
        result["market_cap"] = _f(d.get("f116"))
        result["float_market_cap"] = _f(d.get("f117"))
    except Exception as exc:
        result["profile_error"] = f"{type(exc).__name__}: {exc}"
    try:
        obj2 = _get_json(EM_SLIST, {
            "secid": secid, "fields": "f12,f14", "spt": 3,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        })
        diff = (((obj2 or {}).get("data") or {}).get("diff") or [])
        result["concepts"] = [{"code": str(x.get("f12") or ""), "name": str(x.get("f14") or "")} for x in diff if x.get("f14")]
    except Exception as exc:
        result["concept_error"] = f"{type(exc).__name__}: {exc}"
    _put(key, result)
    return result


def fetch_board_history(board_code: str, count: int = 10) -> List[Dict[str, Any]]:
    board_code = str(board_code).upper().strip()
    key = f"board_hist:{board_code}:{count}"
    c = _cached(key, 900)
    if c is not None:
        return c
    params = {
        "secid": f"90.{board_code}", "klt": 101, "fqt": 0,
        "end": "20500101", "lmt": int(count),
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    obj = _get_json(EM_KLINE, params)
    klines = (((obj or {}).get("data") or {}).get("klines") or [])
    out = []
    for line in klines:
        p = str(line).split(",")
        if len(p) < 9:
            continue
        out.append({
            "date": p[0], "open": _f(p[1]), "close": _f(p[2]), "high": _f(p[3]), "low": _f(p[4]),
            "volume": _f(p[5]), "amount": _f(p[6]), "amplitude": _f(p[7]), "pct": _f(p[8]),
        })
    _put(key, out)
    return out


def build_rotation(sectors: List[Dict[str, Any]], days: int = 7, sample: int = 24) -> Dict[str, Any]:
    # Historical rotation is inferred from current high-heat sectors' own board K-lines.
    # It is explicitly labeled as a sampled path, not an exhaustive all-board reconstruction.
    sample_sectors = sectors[:sample]
    rows: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs = {ex.submit(fetch_board_history, s["code"], days + 3): s for s in sample_sectors}
        for fut in as_completed(jobs):
            s = jobs[fut]
            try:
                h = fut.result()
                if h:
                    rows.append((s, h))
            except Exception:
                pass
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for s, hist in rows:
        for r in hist:
            by_date.setdefault(r["date"], []).append({"code": s["code"], "name": s["name"], "type": s["type"], "pct": r["pct"], "current_heat": s["heat"]})
    dates = sorted(by_date)[-days:]
    timeline = []
    for d in dates:
        arr = sorted(by_date[d], key=lambda x: (x["pct"], x["current_heat"]), reverse=True)
        if not arr:
            continue
        timeline.append({"date": d, "leader": arr[0], "top3": arr[:3]})
    path = " → ".join(x["leader"]["name"] for x in timeline)
    return {
        "timeline": timeline,
        "path": path,
        "sample_size": len(sample_sectors),
        "note": "流转路径按当前高热板块样本回看其历史日涨幅生成；随着每日收盘快照积累，可进一步升级为全板块精确历史路径。",
    }


def build_sector_review(news: Optional[Dict[str, Any]] = None, limit: int = 12) -> Dict[str, Any]:
    started = time.time()
    sectors = get_sector_heat(news)
    top = sectors[:max(limit, 24)]
    rotation = {'timeline': [], 'path': '', 'note': '最近三日轮动只使用本网站已保存的真实每日板块收盘快照；不足三天时不臆造历史路径。'}
    return {
        "sectors": top[:limit],
        "rotation": rotation,
        "total": len(sectors),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "method": "板块涨跌幅横截面 + 上涨家数占比 + 主力净占比/净流入 + 换手活跃度 + 新闻热度辅助",
        "relation_note": "个股归属来自板块成分股接口与个股行业/概念关系，不按股票名称猜测。",
    }


def sector_context_for_stock(code: str, news: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    info = fetch_stock_sector_info(code)
    sectors = get_sector_heat(news)
    by_name = {s["name"]: s for s in sectors}
    rel = []
    names: List[str] = []
    if info.get("industry"):
        names.append(str(info["industry"]))
    names.extend(str(x.get("name")) for x in info.get("concepts") or [] if x.get("name"))
    seen = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        s = by_name.get(name)
        if s:
            rel.append({"name": name, "code": s["code"], "type": s["type"], "heat": s["heat"], "rank": s["rank"], "pct": s["pct"], "breadth": s["breadth"], "main_net_pct": s["main_net_pct"]})
        else:
            rel.append({"name": name, "code": "", "type": "industry" if name == info.get("industry") else "concept", "heat": None, "rank": None, "pct": None, "breadth": None, "main_net_pct": None})
    rel.sort(key=lambda x: (-1 if x["heat"] is None else -x["heat"], x["name"]))
    return {"profile": info, "memberships": rel}
