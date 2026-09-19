from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import multiprocessing as mp
import queue as queue_mod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from hotspot_bridge import fetch_hotspot_desk
from market_data import get_provider
from news_data import build_news_radar, demo_news_radar
from sentiment import build_alerts, build_review, score_sentiment
from stock_selector import build_candidates
from review_selector import build_review_picks
from stock_analysis import analyze_stock
from nextday_selector import build_next5
from sector_engine import build_sector_review, fetch_board_members, sector_context_for_stock

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DB_PATH = Path(os.getenv("DB_PATH", BASE / "sentiment.db"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "75"))
CN_TZ = ZoneInfo("Asia/Shanghai")

app = FastAPI(title="热点链路 × A股短线量价工作台", version="6.8-sector-rotation")
app.add_middleware(GZipMiddleware, minimum_size=700)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "mode": None}
_lock = threading.Lock()

# 真实行情不再占用 HTTP 请求线程。后台独立进程最多运行 LIVE_REFRESH_TIMEOUT 秒；
# 即使第三方 SDK 永久卡住，也能被主进程终止。
LIVE_REFRESH_TIMEOUT = int(os.getenv("LIVE_REFRESH_TIMEOUT", "45"))
LIVE_RETRY_COOLDOWN = int(os.getenv("LIVE_RETRY_COOLDOWN", "30"))
_live_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_refresh: Dict[str, Any] = {
    "process": None, "queue": None, "started": 0.0, "last_attempt": 0.0,
    "state": "idle", "error": None, "elapsed": None,
}
_mp_ctx = mp.get_context("spawn")


@app.middleware("http")
async def public_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_snapshots(
            trade_date TEXT PRIMARY KEY, captured_at TEXT NOT NULL, score REAL NOT NULL,
            stage TEXT NOT NULL, payload TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sector_snapshots(
            trade_date TEXT NOT NULL, board_code TEXT NOT NULL, board_name TEXT NOT NULL,
            heat REAL NOT NULL, pct REAL NOT NULL, breadth REAL NOT NULL, main_net_pct REAL NOT NULL,
            board_type TEXT NOT NULL, captured_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, board_code))""")
        conn.commit()


def save_snapshot(payload: Dict[str, Any]) -> None:
    if not payload.get("trade_date"): return
    slim = {k: payload.get(k) for k in ["trade_date","updated_at","zt_count","zb_count","dt_count","seal_rate","up_count","down_count","max_board"]}
    slim["sentiment"] = payload.get("sentiment")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""INSERT INTO daily_snapshots(trade_date,captured_at,score,stage,payload) VALUES(?,?,?,?,?)
            ON CONFLICT(trade_date) DO UPDATE SET captured_at=excluded.captured_at,score=excluded.score,stage=excluded.stage,payload=excluded.payload""",
            (payload["trade_date"], payload["updated_at"], payload["sentiment"]["score"], payload["sentiment"]["stage"], json.dumps(slim, ensure_ascii=False)))
        conn.commit()


def load_history(limit: int = 20) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows=conn.execute("SELECT trade_date,score,stage FROM daily_snapshots ORDER BY trade_date DESC LIMIT ?",(limit,)).fetchall()
    return [{"date":r[0],"score":r[1],"stage":r[2]} for r in reversed(rows)]


def save_sector_snapshots(trade_date: str, sectors: List[Dict[str, Any]]) -> None:
    if not trade_date or not sectors:
        return
    now = datetime.now(CN_TZ).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        for x in sectors[:40]:
            conn.execute("""INSERT INTO sector_snapshots(trade_date,board_code,board_name,heat,pct,breadth,main_net_pct,board_type,captured_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trade_date,board_code) DO UPDATE SET board_name=excluded.board_name,heat=excluded.heat,pct=excluded.pct,breadth=excluded.breadth,main_net_pct=excluded.main_net_pct,board_type=excluded.board_type,captured_at=excluded.captured_at""",
                (trade_date, x.get("code"), x.get("name"), float(x.get("heat") or 0), float(x.get("pct") or 0), float(x.get("breadth") or 0), float(x.get("main_net_pct") or 0), x.get("type") or "", now))
        conn.commit()

def load_sector_rotation(days: int = 8) -> Dict[str, Any]:
    with sqlite3.connect(DB_PATH) as conn:
        ds = conn.execute("SELECT DISTINCT trade_date FROM sector_snapshots ORDER BY trade_date DESC LIMIT ?", (days,)).fetchall()
        dates = [x[0] for x in reversed(ds)]
        timeline=[]
        for d in dates:
            rows=conn.execute("SELECT board_code,board_name,heat,pct,breadth,main_net_pct,board_type FROM sector_snapshots WHERE trade_date=? ORDER BY heat DESC LIMIT 3", (d,)).fetchall()
            if not rows: continue
            top=[{"code":r[0],"name":r[1],"heat":r[2],"pct":r[3],"breadth":r[4],"main_net_pct":r[5],"type":r[6]} for r in rows]
            timeline.append({"date":d,"leader":top[0],"top3":top})
    return {"timeline":timeline,"path":" → ".join(x["leader"]["name"] for x in timeline),"source":"本网站每日收盘板块快照","note":"随着网站每日收盘运行，流转路径会逐日积累并优先使用真实收盘快照。"}


def build_dashboard(force_demo: bool=False) -> Dict[str, Any]:
    provider=get_provider(force_demo=force_demo)
    market_error=None

    if force_demo:
        market=provider.fetch()
        news=demo_news_radar()
        hotspot={"url":os.getenv("HOTSPOT_DESK_URL","https://hotspot-link-desk.sl604762568.chatgpt.site"),"signals":[],"candidates":[],"error":"演示模式未抓取外部热点链路"}
    else:
        # Three independent networks run in parallel; news/hotspot can never delay market serially.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            fm=ex.submit(provider.fetch)
            fn=ex.submit(build_news_radar)
            fh=ex.submit(fetch_hotspot_desk)
            try:
                market=fm.result()
            except Exception as exc:
                market_error=f"实时行情失败：{type(exc).__name__}: {exc}"
                market=get_provider(force_demo=True).fetch(); market["source"]="行情演示回退"; market["is_live"]=False
            try:
                news=fn.result()
            except Exception:
                news=demo_news_radar(); news["errors"]=(news.get("errors") or [])+["实时新闻抓取失败"]
            try:
                hotspot=fh.result()
            except Exception as exc:
                hotspot={"url":os.getenv("HOTSPOT_DESK_URL","https://hotspot-link-desk.sl604762568.chatgpt.site"),"signals":[],"candidates":[],"error":f"{type(exc).__name__}: {exc}"}
        if not news.get("items"):
            fallback=demo_news_radar(); fallback["errors"]=(news.get("errors") or [])+["所有实时新闻源暂无可用数据，已显示演示新闻结构"]
            news=fallback

    market["sentiment"]=score_sentiment(market)
    market["alerts"]=build_alerts(market,market["sentiment"])
    market["review"]=build_review(market,market["sentiment"])
    market["error"]=market_error
    save_snapshot(market)
    market["history"]=load_history(20)

    history_fetcher=getattr(provider,"history_fetcher",None) if market.get("is_live") else None
    candidates=build_candidates(market,news,hotspot,history_fetcher=history_fetcher,limit=12)

    payload={**market,"news":news,"hotspot":hotspot,"candidates":candidates,"model":{
        "name":"热点×情绪×量价个股研究模型 v6.4","weights":{"量价":40,"热点新闻":25,"市场情绪":20,"强势结构":15},
        "note":"评分代表研究优先度，不预测涨跌；真实源不足时会明确标记，不用演示股票冒充真实候选。"
    }}
    return payload



def _live_worker(out_q) -> None:
    """子进程内执行可能阻塞的所有真实数据抓取。"""
    try:
        data = build_dashboard(force_demo=False)
        out_q.put({"ok": True, "data": data})
    except BaseException as exc:
        out_q.put({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _harvest_refresh() -> None:
    proc = _refresh.get("process")
    q = _refresh.get("queue")
    if proc is None:
        return

    # Queue 有结果时优先收割，不等待子进程自然退出。
    msg = None
    if q is not None:
        try:
            msg = q.get_nowait()
        except queue_mod.Empty:
            pass
        except Exception:
            pass
    if msg is not None:
        elapsed = round(time.time() - float(_refresh.get("started") or time.time()), 2)
        if msg.get("ok") and isinstance(msg.get("data"), dict):
            _live_cache.update({"ts": time.time(), "data": msg["data"]})
            _refresh.update({"state": "ok", "error": None, "elapsed": elapsed})
        else:
            _refresh.update({"state": "error", "error": msg.get("error") or "unknown", "elapsed": elapsed})
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=1)
        _refresh["process"] = None
        _refresh["queue"] = None
        return

    # 硬超时：直接终止整个抓取进程，而不是继续等第三方接口。
    elapsed = time.time() - float(_refresh.get("started") or time.time())
    if proc.is_alive() and elapsed > LIVE_REFRESH_TIMEOUT:
        proc.terminate()
        proc.join(timeout=2)
        _refresh.update({
            "process": None, "queue": None, "state": "timeout",
            "error": f"真实行情后台刷新超过 {LIVE_REFRESH_TIMEOUT}s，已强制终止",
            "elapsed": round(elapsed, 2),
        })
        return

    if not proc.is_alive():
        proc.join(timeout=0.2)
        _refresh.update({"process": None, "queue": None})
        if _refresh.get("state") == "running":
            _refresh.update({"state": "error", "error": "真实行情子进程提前退出且未返回数据"})


def _start_refresh(force: bool = False) -> None:
    _harvest_refresh()
    proc = _refresh.get("process")
    if proc is not None and proc.is_alive():
        return
    now = time.time()
    if not force and now - float(_refresh.get("last_attempt") or 0) < LIVE_RETRY_COOLDOWN:
        return
    q = _mp_ctx.Queue(maxsize=1)
    p = _mp_ctx.Process(target=_live_worker, args=(q,), daemon=True)
    p.start()
    _refresh.update({
        "process": p, "queue": q, "started": now, "last_attempt": now,
        "state": "running", "error": None, "elapsed": None,
    })


def _refresh_meta() -> Dict[str, Any]:
    _harvest_refresh()
    return {
        "state": _refresh.get("state"),
        "error": _refresh.get("error"),
        "elapsed": _refresh.get("elapsed"),
        "timeout_seconds": LIVE_REFRESH_TIMEOUT,
        "has_live_cache": bool(_live_cache.get("data")),
        "live_cache_age": round(time.time() - float(_live_cache.get("ts") or time.time()), 1) if _live_cache.get("data") else None,
    }


@app.on_event("startup")
def _startup():
    init_db()

@app.get("/")
def index(): return FileResponse(STATIC/"index.html")
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(STATIC/"manifest.webmanifest")
@app.get("/sw.js")
def sw(): return FileResponse(STATIC/"sw.js", media_type="application/javascript")
@app.get("/icon.svg")
def icon(): return FileResponse(STATIC/"icon.svg", media_type="image/svg+xml")

@app.get("/api/dashboard")
def dashboard(mode: str=Query("auto",pattern="^(auto|demo)$"), fresh: bool=False):
    now = time.time()
    if mode == "demo":
        with _lock:
            if (not fresh and _cache.get("data") is not None and _cache.get("mode") == "demo"
                    and now - float(_cache.get("ts", 0)) < CACHE_SECONDS):
                data = dict(_cache["data"])
            else:
                data = build_dashboard(force_demo=True)
                _cache.update({"ts": now, "data": data, "mode": "demo"})
        data = dict(data)
        data["refresh_status"] = _refresh_meta()
        return JSONResponse(data)

    # AUTO 模式永不直接执行网络抓取。先收割后台结果，再按需触发后台刷新。
    _harvest_refresh()
    live = _live_cache.get("data")
    live_age = now - float(_live_cache.get("ts") or 0) if live else 10**9
    if fresh or live is None or live_age >= CACHE_SECONDS:
        _start_refresh(force=fresh)

    if live is not None:
        data = dict(live)
        data["refresh_status"] = _refresh_meta()
        data["served_from"] = "live-cache"
        return JSONResponse(data)

    # 冷启动期间立即返回演示/结构数据，前端随后轮询拿真实结果。
    with _lock:
        if (_cache.get("data") is not None and _cache.get("mode") == "demo"
                and now - float(_cache.get("ts", 0)) < CACHE_SECONDS):
            data = dict(_cache["data"])
        else:
            data = build_dashboard(force_demo=True)
            _cache.update({"ts": now, "data": data, "mode": "demo"})
    data["refresh_status"] = _refresh_meta()
    data["served_from"] = "instant-fallback"
    data["notice"] = "真实行情正在后台刷新；本次请求没有等待第三方接口。"
    return JSONResponse(data)

@app.get("/api/review-picks")
def review_picks(mode: str=Query("auto",pattern="^(auto|demo)$"), limit: int=Query(10,ge=3,le=20)):
    if mode == "demo":
        return JSONResponse({"regime": {"level":"--","score":0,"note":"演示模式不输出虚构复盘个股"}, "picks":[], "chan_picks":[], "scanned":0, "error":"请切换实时模式后执行复盘。"})
    _harvest_refresh()
    data=_live_cache.get("data")
    if not data:
        _start_refresh(force=False)
        return JSONResponse({"regime": {"level":"--","score":0,"note":"真实行情正在后台刷新"}, "picks":[], "chan_picks":[], "scanned":0, "error":"真实行情尚未准备好，请等待数据源状态变为实时后再点一次。"})
    try:
        from public_sources import fetch_history_df
        result=build_review_picks(data, data.get("news") or {}, history_fetcher=fetch_history_df, limit=limit)
        result["trade_date"]=data.get("trade_date")
        result["updated_at"]=datetime.now(CN_TZ).isoformat(timespec="seconds")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"regime":{},"picks":[],"chan_picks":[],"scanned":0,"error":f"复盘选股失败：{type(exc).__name__}: {exc}"})

@app.get("/api/stock/{code}")
def stock_detail(code: str):
    code=''.join(ch for ch in code if ch.isdigit())[:6].zfill(6)
    try:
        from public_sources import fetch_history_df
        df, source=fetch_history_df(code, 90)
        if df is None or df.empty: return JSONResponse({"code":code,"rows":[],"error":"暂无K线"})
        rows=[]
        for _,r in df.tail(70).iterrows():
            rows.append({"date":str(r.get("date","")),"open":float(r.get("open",0)),"close":float(r.get("close",0)),"high":float(r.get("high",0)),"low":float(r.get("low",0)),"volume":float(r.get("volume",0)),"amount":float(r.get("amount",0) or 0)})
        return {"code":code,"rows":rows,"source":source,"error":None}
    except Exception as exc:
        return JSONResponse({"code":code,"rows":[],"error":f"{type(exc).__name__}: {exc}"})


@app.get("/api/next5")
def next5():
    _harvest_refresh()
    data=_live_cache.get("data")
    if not data:
        _start_refresh(force=False)
        return JSONResponse({"environment":{},"picks":[],"scanned":0,"error":"真实行情尚未准备好，请稍后再试。"})
    try:
        from public_sources import fetch_history_df
        result=build_next5(data, data.get("news") or {}, fetch_history_df, limit=5)
        result["updated_at"]=datetime.now(CN_TZ).isoformat(timespec="seconds")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"environment":{},"picks":[],"scanned":0,"error":f"次日联动筛选失败：{type(exc).__name__}: {exc}"})

@app.get("/api/search")
def search_stock(q: str=Query("", min_length=1, max_length=32), limit: int=Query(12,ge=1,le=30)):
    _harvest_refresh()
    data=_live_cache.get("data") or {}
    items=(data.get("review_universe") or data.get("active_stocks") or [])
    key=q.strip().lower()
    out=[]
    for s in items:
        code=str(s.get("code","")).zfill(6); name=str(s.get("name","")).strip()
        if key in code.lower() or key in name.lower():
            out.append({"code":code,"name":name,"industry":s.get("industry","") or "","price":s.get("price"),"pct":s.get("pct")})
        if len(out)>=limit: break
    # Exact six-digit code remains analyzable even if it is not in the current snapshot.
    if not out and q.strip().isdigit() and len(q.strip())<=6:
        out.append({"code":q.strip().zfill(6),"name":"","industry":"","price":None,"pct":None})
    return {"q":q,"items":out}

@app.get("/api/analyze/{code}")
def analyze_one(code: str):
    code=''.join(ch for ch in code if ch.isdigit())[:6].zfill(6)
    _harvest_refresh()
    data=_live_cache.get("data") or {}
    info={}
    for s in (data.get("review_universe") or data.get("active_stocks") or []):
        if str(s.get("code","")).zfill(6)==code:
            info=s; break
    news=data.get("news") or {}
    try:
        relation=sector_context_for_stock(code, news=news)
    except Exception as exc:
        relation={"profile":{"code":code,"name":"","industry":"","concepts":[],"error":str(exc)},"memberships":[]}
    profile=relation.get("profile") or {}
    name=str(info.get("name") or profile.get("name") or "")
    industry=str(profile.get("industry") or info.get("industry") or "")
    relation_names=[x.get("name") for x in relation.get("memberships") or [] if x.get("name")]
    event_hits=[]
    for item in (news.get("items") or [])[:160]:
        title=str(item.get("title", ""))
        if (name and name in title) or (industry and industry in title) or any(rn and rn in title for rn in relation_names[:12]):
            event_hits.append(title)
        if len(event_hits)>=8: break
    try:
        from public_sources import fetch_history_df
        df, source=fetch_history_df(code, 180)
        sent=data.get("sentiment") or {}
        result=analyze_stock(
            df, code=code, name=name, industry=industry, event_hits=event_hits,
            market_score=sent.get("score"), market_stage=str(sent.get("stage", "")),
        )
        result["source"]=source
        result["snapshot"]={k:info.get(k) for k in ["price","pct","amount","turnover_rate","volume_ratio","high","low","open","prev_close","market_cap","float_market_cap"]} if info else {}
        result["sector_relations"]=relation.get("memberships") or []
        hotrels=[x for x in result["sector_relations"] if x.get("heat") is not None]
        result["industry_analysis"]={
            "industry":industry,
            "hot_memberships":hotrels[:8],
            "summary": (f"当前可核验到 {len(result['sector_relations'])} 个行业/概念关系；其中热度最高的是 {hotrels[0]['name']}（热度 {hotrels[0]['heat']:.1f}，板块排名 {hotrels[0]['rank']}）。" if hotrels else f"当前可核验到 {len(result['sector_relations'])} 个行业/概念关系，暂无对应板块热度快照。"),
            "relation_source":profile.get("source") or "东方财富个股行业/概念关系",
        }
        result["company_analysis"]={
            "name":name,"code":code,"industry":industry,
            "concepts":[x.get("name") for x in profile.get("concepts") or [] if x.get("name")][:20],
            "market_cap": profile.get("market_cap") or info.get("market_cap"),
            "float_market_cap": profile.get("float_market_cap") or info.get("float_market_cap"),
            "activity": {"amount":info.get("amount"),"turnover_rate":info.get("turnover_rate"),"volume_ratio":info.get("volume_ratio")},
            "summary":"公司分析先展示真实行业/概念归属、流动性和交易活跃度；不根据公司名称猜题材。基本面财报深挖可在后续版本继续接入。",
        }
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"code":code,"name":name,"rows":[],"sector_relations":relation.get("memberships") or [],"error":f"单股分析失败：{type(exc).__name__}: {exc}"})

@app.get("/api/sector-review")
def sector_review(limit: int=Query(12,ge=6,le=24)):
    _harvest_refresh()
    data=_live_cache.get("data") or {}
    news=data.get("news") or {}
    try:
        out=build_sector_review(news=news, limit=limit)
        trade_date=str(data.get("trade_date") or datetime.now(CN_TZ).date().isoformat())
        save_sector_snapshots(trade_date, out.get("sectors") or [])
        stored=load_sector_rotation(8)
        if len(stored.get("timeline") or []) >= 2:
            out["rotation_sample"] = out.get("rotation")
            out["rotation"] = stored
        out["trade_date"] = trade_date
        return JSONResponse(out)
    except Exception as exc:
        return JSONResponse({"sectors":[],"rotation":{"timeline":[],"path":""},"error":f"板块复盘失败：{type(exc).__name__}: {exc}"})

@app.get("/api/sector/{board_code}")
def sector_detail(board_code: str, limit: int=Query(200,ge=20,le=500)):
    board_code=board_code.strip().upper()
    try:
        members=fetch_board_members(board_code, limit=limit)
        review=build_sector_review(news=(_live_cache.get("data") or {}).get("news") or {}, limit=24)
        board=next((x for x in review.get("sectors") or [] if x.get("code")==board_code), None)
        return JSONResponse({"board":board or {"code":board_code},"members":members,"count":len(members),"relation_source":"东方财富板块成分关系","note":"成分股来自板块关系接口，不根据公司名称关键词推断。"})
    except Exception as exc:
        return JSONResponse({"board":{"code":board_code},"members":[],"count":0,"error":f"板块成分股失败：{type(exc).__name__}: {exc}"})


@app.get("/api/sources")
def sources_probe():
    from public_sources import probe_sources
    started=time.time()
    probes=probe_sources()
    return {"ok":any(x.get("ok") for x in probes.values()),"elapsed_ms":int((time.time()-started)*1000),"sources":probes}

@app.get("/api/health")
def health():
    db_ok = True
    db_error = None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        db_ok = False
        db_error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": db_ok,
        "version": "6.8-sector-rotation",
        "time": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "cache_seconds": CACHE_SECONDS,
        "db": {"ok": db_ok, "path": str(DB_PATH), "error": db_error},
        "refresh": _refresh_meta(),
    }
