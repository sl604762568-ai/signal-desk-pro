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
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request, Header, HTTPException
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
from review_engine import build_close_review
from v611_features import (init as init_v611, freeze_get, freeze_put, freeze_latest, watch_add, watch_list, watch_remove, auction_capture, record_daily_close, auction_view, tech_start, tech_results, tech_start_from_source, now as now_cn)
from v611_dragon import fetch_dragons
from v611_auction_analysis import init as init_auction_topics, build_topics, get_topics, replay_real_archive
from paper_trader import (init_paper_db, get_settings as get_paper_settings, save_settings as save_paper_settings,
                          reset_account as reset_paper_account, store_signals as store_paper_signals,
                          has_signals_for, run_engine as run_paper_engine, get_portfolio as get_paper_portfolio,
                          performance as get_paper_performance, manual_buy as paper_manual_buy,
                          manual_sell as paper_manual_sell, get_watch_codes as get_paper_watch_codes)

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DB_PATH = Path(os.getenv("DB_PATH", BASE / "sentiment.db"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "75"))
CN_TZ = ZoneInfo("Asia/Shanghai")

app = FastAPI(title="热点链路 × A股短线量价工作台", version="6.11.1-nonblocking-review")
app.add_middleware(GZipMiddleware, minimum_size=700)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "mode": None}
_dragon_cache: Dict[str, Any] = {}
_sector_last: Dict[str,Any]={'data':None,'ts':0}
_sector_lock=threading.RLock()
from concurrent.futures import ThreadPoolExecutor
_sector_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='board-review')
_lock = threading.Lock()

# 真实行情不再占用 HTTP 请求线程。后台独立进程最多运行 LIVE_REFRESH_TIMEOUT 秒；
# 即使第三方 SDK 永久卡住，也能被主进程终止。
LIVE_REFRESH_TIMEOUT = int(os.getenv("LIVE_REFRESH_TIMEOUT", "18"))
LIVE_RETRY_COOLDOWN = int(os.getenv("LIVE_RETRY_COOLDOWN", "15"))
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
    init_v611()
    init_auction_topics()
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


def build_dashboard(force_demo: bool=False, fast_live: bool=False) -> Dict[str, Any]:
    provider=get_provider(force_demo=force_demo)
    market_error=None

    if force_demo:
        market=provider.fetch()
        news=demo_news_radar()
        hotspot={"url":os.getenv("HOTSPOT_DESK_URL","https://hotspot-link-desk.sl604762568.chatgpt.site"),"signals":[],"candidates":[],"error":"演示模式未抓取外部热点链路"}
    elif fast_live:
        market=provider.fetch(fast=True)
        news={'items':[],'clusters':[],'source_counts':{},'errors':[],'elapsed_ms':0}
        hotspot={'url':os.getenv('HOTSPOT_DESK_URL','https://hotspot-link-desk.sl604762568.chatgpt.site'),
                 'signals':[],'candidates':[],'error':None}
    else:
        # Deep news/hotspot gathering belongs to on-demand endpoints only.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            fm=ex.submit(provider.fetch, fast=fast_live)
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

    history_fetcher=(None if fast_live else getattr(provider,"history_fetcher",None)) if market.get("is_live") else None
    candidates=build_candidates(market,news,hotspot,history_fetcher=history_fetcher,limit=12)

    payload={**market,"news":news,"hotspot":hotspot,"candidates":candidates,"model":{
        "name":"热点×情绪×量价个股研究模型 v6.4","weights":{"量价":40,"热点新闻":25,"市场情绪":20,"强势结构":15},
        "note":"评分代表研究优先度，不预测涨跌；真实源不足时会明确标记，不用演示股票冒充真实候选。"
    }}
    return payload



def _live_worker(out_q) -> None:
    """子进程内执行可能阻塞的所有真实数据抓取。"""
    try:
        data = build_dashboard(force_demo=False, fast_live=True)
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


_paper_loop_started = False

def _paper_sync_signals(data: Dict[str, Any], force: bool=False) -> Dict[str, Any]:
    trade_date=str(data.get("trade_date") or datetime.now(CN_TZ).date().isoformat())[:10]
    if (not force) and has_signals_for(DB_PATH, trade_date):
        return {"ok":True,"saved":0,"trade_date":trade_date,"note":"当日次日5股已保存"}
    from public_sources import fetch_history_df
    result=build_next5(data, data.get("news") or {}, fetch_history_df, limit=5)
    picks=result.get("picks") or []
    saved=store_paper_signals(DB_PATH, trade_date, picks)
    return {"ok":True,"saved":saved,"trade_date":trade_date,"environment":result.get("environment") or {},"picks":picks}

def _paper_background_loop():
    # 纯虚拟盘后台循环。免费 Render 休眠时不会运行，因此前端也会定时触发 /api/paper/run。
    while True:
        try:
            cfg=get_paper_settings(DB_PATH)
            if cfg.get("auto_enabled"):
                _harvest_refresh()
                data=_live_cache.get("data")
                if data:
                    now=datetime.now(CN_TZ)
                    # 15:05 后保存当天“次日5股”，只做一次。
                    if (now.hour>15 or (now.hour==15 and now.minute>=5)):
                        try: _paper_sync_signals(data, force=False)
                        except Exception: pass
                    # 持仓风控和次日信号入场使用当前实时快照。
                    try: run_paper_engine(DB_PATH, data)
                    except Exception: pass
        except Exception:
            pass
        time.sleep(60)

def _auction_heartbeat():
    """Best-effort capture while instance is awake; no guarantee on Render Free suspension."""
    last=None
    last_close=None
    while True:
        t=now_cn()
        day=t.date().isoformat()
        if t.weekday()<5 and t.hour==9 and t.minute==25 and day!=last:
            outcome=auction_capture()
            if outcome.get('ok'):
                last=day
                try:build_topics(day,news=_news_api_cache.get('data') or {'items':[]},max_stocks=80)
                except Exception:pass
        if t.weekday()<5 and t.hour==15 and 5<=t.minute<=29 and day!=last_close:
            close_result=record_daily_close()
            if close_result.get('ok'):last_close=day
        time.sleep(8 if t.hour==9 and 23<=t.minute<=26 else 60)

@app.on_event("startup")
def _startup():
    init_db()
    init_paper_db(DB_PATH)
    global _paper_loop_started
    if not _paper_loop_started:
        _paper_loop_started = True
        threading.Thread(target=_paper_background_loop, daemon=True, name="paper-trading-loop").start()
    threading.Thread(target=_auction_heartbeat,daemon=True,name='auction-925-heartbeat').start()

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



# 收盘复盘不再在 HTTP 请求线程里等待多路公开数据。
# 同时最多允许一次采集；若某个外部源永久无响应，请求仍然可以快速返回。
_close_review_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_close_review_state: Dict[str, Any] = {"started": 0.0, "running": False, "error": None, "attempt": 0}
_close_review_lock = threading.RLock()
CLOSE_REVIEW_BUDGET = max(10, min(120, int(os.getenv("CLOSE_REVIEW_BUDGET", "35"))))


def _close_review_worker() -> None:
    result = None
    try:
        result = build_close_review()
        if not isinstance(result, dict):
            result = {"ok": False, "error": "收盘复盘返回格式异常"}
    except Exception as exc:
        result = {"ok": False, "verified": False,
                  "error": f"收盘复盘采集异常：{type(exc).__name__}: {exc}"}
    finally:
        with _close_review_lock:
            _close_review_cache.update({"ts": time.time(), "data": result})
            _close_review_state.update({"running": False,
                                        "error": result.get("error") if result else "empty result"})


@app.get("/api/close-review")
def close_review(fresh: bool = False):
    now = time.time()
    with _close_review_lock:
        data = _close_review_cache.get("data")
        age = now - float(_close_review_cache.get("ts") or 0)
        is_running = bool(_close_review_state.get("running"))
        running_age = now - float(_close_review_state.get("started") or now)
        if data is not None and not fresh and age < 180:
            return JSONResponse({**data, "cache_age_seconds": round(age, 1)})
        if is_running:
            if running_age >= CLOSE_REVIEW_BUDGET:
                # 不伪造涨跌停数字，也不启动第二轮重型采集。
                return JSONResponse({"ok": False, "loading": False, "state": "source_timeout",
                    "error": "公开复盘数据源超时；后台仍在等待该源。不会发布未校验数字。",
                    "elapsed_seconds": round(running_age, 1)})
            return JSONResponse({"ok": False, "loading": True, "state": "collecting",
                                 "elapsed_seconds": round(running_age, 1)})
        # 失败后短暂冷却，避免多个手机/页面并发打爆免费实例。
        if data is not None and age < 25:
            return JSONResponse({**data, "cache_age_seconds": round(age, 1)})
        _close_review_state.update({"running": True, "started": now,
                                   "error": None, "attempt": _close_review_state["attempt"] + 1})
        threading.Thread(target=_close_review_worker, daemon=True,
                         name="close-review-collector").start()
    return JSONResponse({"ok": False, "loading": True, "state": "collecting",
                         "elapsed_seconds": 0})


@app.get("/api/review-picks")
def review_picks(mode: str=Query("auto",pattern="^(auto|demo)$"), limit: int=Query(10,ge=3,le=20)):
    today=now_cn().date().isoformat()
    frozen=freeze_get(today,'review')
    if frozen and mode!='demo':return JSONResponse(frozen)
    if mode!='demo' and now_cn().hour<15:
        archived=freeze_latest('review',today)
        if archived:return JSONResponse({**archived,'note':'显示最近一次真实收盘固定复盘，盘中刷新不重新选股'})
    if mode == "demo":
        return JSONResponse({"regime": {"level":"--","score":0,"note":"演示模式不输出虚构复盘个股"}, "picks":[], "chan_picks":[], "scanned":0, "error":"请切换实时模式后执行复盘。"})
    _harvest_refresh()
    data=_live_cache.get("data")
    if not data:
        _start_refresh(force=False)
        return JSONResponse({"regime": {"level":"--","score":0,"note":"真实行情正在后台刷新"}, "picks":[], "chan_picks":[], "scanned":0, "error":"真实行情尚未准备好，请等待数据源状态变为实时后再点一次。"})
    try:
        from public_sources import fetch_history_df
        news=_get_cached_news()
        result=build_review_picks(data, news, history_fetcher=fetch_history_df, limit=limit)
        result["trade_date"]=data.get("trade_date")
        if now_cn().hour>=15 and str(data.get('trade_date') or '').replace('-','')[:8]==today.replace('-',''):
            freeze_put(today,'review',result)
            result=freeze_get(today,'review') or result
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
    today=now_cn().date().isoformat()
    frozen=freeze_get(today,'next5')
    if frozen:return JSONResponse(frozen)
    if now_cn().hour<15:
        archived=freeze_latest('next5',today)
        if archived:return JSONResponse({**archived,'note':'显示最近收盘固定的次日五股，盘中刷新不改变名单'})
    _harvest_refresh()
    data=_live_cache.get("data")
    if not data:
        _start_refresh(force=False)
        return JSONResponse({"environment":{},"picks":[],"scanned":0,"error":"真实行情尚未准备好，请稍后再试。"})
    try:
        from public_sources import fetch_history_df
        news=_get_cached_news()
        result=build_next5(data, news, fetch_history_df, limit=5)
        result["updated_at"]=datetime.now(CN_TZ).isoformat(timespec="seconds")
        result["trade_date"]=data.get('trade_date')
        current_cn=datetime.now(CN_TZ)
        if current_cn.hour>=15 and str(data.get('trade_date') or '').replace('-','')[:8]==today.replace('-',''):
            freeze_put(today,'next5',result)
            result=freeze_get(today,'next5') or result
        now_cn=current_cn
        if now_cn.hour>15 or (now_cn.hour==15 and now_cn.minute>=0):
            try:
                sd=str(data.get("trade_date") or now_cn.date().isoformat())[:10]
                result["paper_saved"]=store_paper_signals(DB_PATH, sd, result.get("picks") or [])
            except Exception as _paper_exc:
                result["paper_save_error"]=str(_paper_exc)
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

def _sector_background_done(future,trade_date):
    try:
        out=future.result()
        save_sector_snapshots(trade_date,out.get('sectors') or [])
        stored=load_sector_rotation(3)
        if len(stored.get('timeline') or [])>=2:
            out['rotation_sample']=out.get('rotation')
            out['rotation']=stored
        out['trade_date']=trade_date
        with _sector_lock:_sector_last.update(data=out,ts=time.time(),future=None,error=None)
    except Exception as exc:
        with _sector_lock:_sector_last.update(future=None,error=f'{type(exc).__name__}: {exc}',error_ts=time.time())

@app.get('/api/sector-review')
def sector_review(limit:int=Query(12,ge=6,le=24)):
    _harvest_refresh()
    data=_live_cache.get('data') or {}
    news=data.get('news') or {}
    trade_date=str(data.get('trade_date') or now_cn().date().isoformat())
    with _sector_lock:
        old=_sector_last.get('data');age=time.time()-_sector_last.get('ts',0)
        running=_sector_last.get('future')
        if old and age<480:return JSONResponse({**old,'cached':True})
        if running is None and not (_sector_last.get('error') and time.time()-_sector_last.get('error_ts',0)<90):
            future=_sector_pool.submit(build_sector_review,news=news,limit=limit)
            _sector_last['future']=future
            future.add_done_callback(lambda f:_sector_background_done(f,trade_date))
        error=_sector_last.get('error')
    if old:return JSONResponse({**old,'cached':True,'stale':True,'refreshing':True})
    if error and time.time()-_sector_last.get('error_ts',0)<90:return JSONResponse({'sectors':[],'rotation':{'timeline':[]},'error':'板块数据源本轮不可用，请稍后刷新：'+error})
    return JSONResponse({'sectors':[],'rotation':{'timeline':[]},'loading':True,
        'note':error or '正在独立加载真实板块资金和成分归属；不会阻塞大盘行情'})

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


class PaperSettingsInput(BaseModel):
    initial_capital: float | None = None
    max_positions: int | None = None
    position_pct: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    max_hold_days: int | None = None
    min_pick_score: float | None = None
    commission_rate: float | None = None
    min_commission: float | None = None
    stamp_duty_rate: float | None = None
    slippage_rate: float | None = None
    auto_enabled: bool | None = None

class PaperResetInput(BaseModel):
    initial_capital: float = 10000

class PaperManualBuyInput(BaseModel):
    code: str
    qty: int | None = None
    amount: float | None = None

class PaperManualSellInput(BaseModel):
    code: str
    qty: int | None = None
    all: bool = False

def _paper_market_for_codes(codes: List[str]) -> Dict[str, Any]:
    from public_sources import fetch_tencent_quotes
    rows, meta = fetch_tencent_quotes(codes)
    now = datetime.now(CN_TZ)
    return {
        "source": meta.get("provider") or "腾讯财经实时",
        "quote_meta": meta,
        "is_live": bool(rows),
        "trade_date": now.date().isoformat(),
        "updated_at": now.isoformat(timespec="seconds"),
        "review_universe": rows,
        "active_stocks": rows,
    }

@app.get("/api/paper")
def paper_status():
    codes=get_paper_watch_codes(DB_PATH, include_signals=False)
    data=_paper_market_for_codes(codes) if codes else {}
    out=get_paper_portfolio(DB_PATH, data)
    out["quote_source"]=(data.get("quote_meta") or {}).get("provider") if data else None
    out["quote_updated_at"]=data.get("updated_at") if data else None
    out["quote_errors"]=(data.get("quote_meta") or {}).get("errors") if data else []
    return JSONResponse(out)

@app.post("/api/paper/settings")
def paper_settings(inp: PaperSettingsInput):
    updates={k:v for k,v in inp.model_dump().items() if v is not None}
    cfg=save_paper_settings(DB_PATH, updates)
    return JSONResponse({"ok":True,"settings":cfg})

@app.post("/api/paper/reset")
def paper_reset(inp: PaperResetInput):
    return JSONResponse({"ok":True,"portfolio":reset_paper_account(DB_PATH, inp.initial_capital)})

@app.post("/api/paper/sync-picks")
def paper_sync_picks(force: bool=False):
    _harvest_refresh()
    data=_live_cache.get("data")
    if not data:
        _start_refresh(force=False)
        return JSONResponse({"ok":False,"error":"真实行情尚未准备好"})
    try:
        return JSONResponse(_paper_sync_signals(data, force=force))
    except Exception as exc:
        return JSONResponse({"ok":False,"error":f"保存次日5股失败：{type(exc).__name__}: {exc}"})

@app.post("/api/paper/run")
def paper_run():
    try:
        # 自动/手动纸面交易只拉“持仓 + 最近候选”少量股票的腾讯实时行情，
        # 不再等待全A快照，避免第三方全市场分页拖死虚拟盘。
        codes=get_paper_watch_codes(DB_PATH, include_signals=True)
        data=_paper_market_for_codes(codes) if codes else {
            "source":"腾讯财经实时","is_live":False,"trade_date":datetime.now(CN_TZ).date().isoformat(),
            "updated_at":datetime.now(CN_TZ).isoformat(timespec="seconds"),"review_universe":[],"active_stocks":[]
        }
        if codes and not data.get("review_universe"):
            return JSONResponse({"ok":False,"error":"腾讯实时行情暂未返回关注股票报价，未执行虚拟交易", "quote_meta":data.get("quote_meta")})
        return JSONResponse(run_paper_engine(DB_PATH, data))
    except Exception as exc:
        return JSONResponse({"ok":False,"error":f"虚拟盘执行失败：{type(exc).__name__}: {exc}"})

@app.get("/api/paper/quote/{code}")
def paper_quote(code: str):
    code=''.join(ch for ch in code if ch.isdigit())[:6].zfill(6)
    data=_paper_market_for_codes([code])
    rows=data.get("review_universe") or []
    if not rows:
        return JSONResponse({"ok":False,"code":code,"error":"未取得该股票实时报价","meta":data.get("quote_meta")})
    return JSONResponse({"ok":True,"quote":rows[0],"source":data.get("source"),"updated_at":data.get("updated_at")})

@app.post("/api/paper/manual-buy")
def paper_manual_buy_route(inp: PaperManualBuyInput):
    code=''.join(ch for ch in inp.code if ch.isdigit())[:6].zfill(6)
    data=_paper_market_for_codes([code])
    rows=data.get("review_universe") or []
    if not rows:
        return JSONResponse({"ok":False,"error":"未取得该股票实时报价，未执行虚拟买入","meta":data.get("quote_meta")})
    result=paper_manual_buy(DB_PATH, rows[0], qty=inp.qty, amount=inp.amount)
    result["portfolio"]=get_paper_portfolio(DB_PATH, data)
    result["quote_source"]=data.get("source")
    return JSONResponse(result)

@app.post("/api/paper/manual-sell")
def paper_manual_sell_route(inp: PaperManualSellInput):
    code=''.join(ch for ch in inp.code if ch.isdigit())[:6].zfill(6)
    data=_paper_market_for_codes([code])
    rows=data.get("review_universe") or []
    if not rows:
        return JSONResponse({"ok":False,"error":"未取得该股票实时报价，未执行虚拟卖出","meta":data.get("quote_meta")})
    result=paper_manual_sell(DB_PATH, rows[0], qty=inp.qty, sell_all=inp.all)
    result["portfolio"]=get_paper_portfolio(DB_PATH, data)
    result["quote_source"]=data.get("source")
    return JSONResponse(result)

@app.get("/api/paper/performance")
def paper_performance(days: int=Query(30,ge=1,le=3650)):
    return JSONResponse(get_paper_performance(DB_PATH, days))

@app.get("/api/sources")
def sources_probe():
    from public_sources import probe_sources
    started=time.time()
    probes=probe_sources()
    return {"ok":any(x.get("ok") for x in probes.values()),"elapsed_ms":int((time.time()-started)*1000),"sources":probes}


# v6.11 control: public readers may view; privileged writes require an owner secret
# (configure CONTROL_TOKEN in Render Environment; never commit it to GitHub).
def _require_owner(token: str | None):
    import hmac
    expected=os.getenv('CONTROL_TOKEN','')
    if not expected:raise HTTPException(503,'请先在Render设置CONTROL_TOKEN，然后才能修改自选或启动全市场扫描')
    if not token or not hmac.compare_digest(str(token),expected):raise HTTPException(403,'控制口令错误')

class WatchInput(BaseModel):
    code: str
    source: str='手动自选'
    trigger_date: str | None=None
    trigger_conditions: List[str]=[]

@app.get('/api/watchlist')
def get_watchlist():return JSONResponse(watch_list())

@app.post('/api/watchlist')
def post_watchlist(inp:WatchInput,x_control_token: str | None=Header(None)):
    _require_owner(x_control_token)
    try:return JSONResponse(watch_add(inp.code,inp.source,inp.trigger_date,inp.trigger_conditions))
    except Exception as exc:raise HTTPException(422,str(exc))

@app.delete('/api/watchlist/{code}')
def delete_watchlist(code:str,x_control_token: str | None=Header(None)):
    _require_owner(x_control_token);watch_remove(code);return {'ok':True}

def enrich_auction_research(raw):
    # Six independently sourced descriptive factors. Do not fabricate missing archives.
    day=raw.get('trade_date')
    try:
        with sqlite3.connect(DB_PATH,timeout=8) as c:
            prior=c.execute('SELECT trade_date,score FROM daily_snapshots WHERE trade_date<? ORDER BY trade_date DESC LIMIT 1',(day,)).fetchone()
            market_score=prior[1] if prior else None
            historical_codes=set()
            if prior and prior[0]==TECH.get('day') and TECH.get('status')=='complete':
                historical_codes={x[0] for x in c.execute('SELECT code FROM technical_hits WHERE trade_date=?',(prior[0],))}
                historical_known=True
            else: historical_known=False
        topics=get_topics(day)
        topic_known=topics.get('state')=='complete'
        theme_codes={}
        if topic_known:
            for x in topics.get('topics',[]):
                for st in x.get('stocks',[]):
                    code=st.get('code')
                    theme_codes[code]=max(theme_codes.get(code,0),int(x.get('stock_count') or 0))
        for row in raw.get('items',[]):
            boom=row.get('boom');gap=row.get('gap');turn=row.get('turnover')
            factors={
              'amount':min(100,round(float(row['amount'])/float(row.get('dynamic_amount_floor') or 5e6)*40,1)) if boom is not None else None,
              'turnover':min(100,round(turn/.75*100,1)) if turn is not None else None,
              'gap':max(0,round(100-abs(gap-4)*20,1)) if gap is not None else None,
              'history':(100 if row['code'] in historical_codes else 0) if historical_known else None,
              'topic':min(100,35+max(0,theme_codes[row['code']]-1)*32) if topic_known and row['code'] in theme_codes else None,
              'market':round(max(0,min(100,market_score)),1) if market_score is not None else None}
            row['factor_scores']=factors
            row['factor_explanation']='研究关注分仅使用已归档竞价、上一交易日情绪、已完成历史股性扫描和可追溯题材关系；缺任何一项不发布总分。'
            row['score_complete']=all(v is not None for v in factors.values())
        raw['factor_model']='描述性研究关注分；分数≠上涨概率；权重由浏览器界面调整，不影响原始行情。'
    except Exception as e:
        raw['factor_model']='历史研究维度暂不可用，未发布任何未经核实的总分。'
        for row in raw.get('items',[]):row.update(factor_scores={},score_complete=False)
    return raw

@app.get('/api/auction25')
def get_auction25(trade_date:str|None=None, min_amount:float=5000000,min_boom:float=2,
                  min_turnover:float=.15,min_gap:float=1,max_gap:float=7,min_prev_day_pct:float=2,max_price:float|None=None,
                  sort:str='amount',limit:int=80,watch_only:bool=False):
    raw=auction_view(locals())
    return JSONResponse(enrich_auction_research(raw))

@app.get('/api/auction25/topics')
def auction_topics(trade_date:str|None=None):
    return JSONResponse(get_topics(trade_date or now_cn().date().isoformat()))

@app.post('/api/auction25/topics/analyze')
def auction_topics_analyze(trade_date:str|None=None,x_control_token:str|None=Header(None)):
    _require_owner(x_control_token)
    day=trade_date or now_cn().date().isoformat()
    # Theme classification uses only documented provider membership, not stock names.
    # News retrieval runs separately and can be absent without affecting quote publication.
    cached=_news_api_cache.get('data') or {'items':[]}
    return JSONResponse(build_topics(day,news=cached,max_stocks=80))

@app.get('/api/auction25/backtest')
def auction_backtest(min_amount:float=5000000,min_boom:float=2,min_turnover:float=.15,
    min_gap:float=1,max_gap:float=7,max_price:float=80,min_prev_day_pct:float=2,days:int=90):
    return JSONResponse(replay_real_archive(min_amount,min_boom,min_turnover,min_gap,max_gap,max_price,
          min_prev_day_pct,max(2,min(days,365))))

@app.post('/api/auction25/capture')
def capture_auction25(x_control_token:str|None=Header(None)):
    _require_owner(x_control_token)
    result=auction_capture()
    if result.get('ok'):
        try:build_topics(result.get('trade_date'),news=_news_api_cache.get('data') or {'items':[]},max_stocks=80)
        except Exception:pass
    return JSONResponse(result)

_news_api_cache={'data':None,'ts':0,'future':None}
_news_api_lock=threading.Lock()
_news_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='independent-news')
def _get_cached_news():
    # Avoid a multi-source news timeout blocking live dashboard or next-day research.
    with _news_api_lock:
        if _news_api_cache.get('data') and time.time()-_news_api_cache['ts']<300:
            return _news_api_cache['data']
        fut=_news_api_cache.get('future')
        if fut is None or (fut.done() and _news_api_cache.get('ts',0)<time.time()-300):
            fut=_news_pool.submit(build_news_radar)
            _news_api_cache['future']=fut
    try:data=fut.result(timeout=4)
    except Exception as exc:
        # Future remains running; next request reuses it rather than launching another fetch.
        if fut.done():
            data={'items':[],'clusters':[],'error':f'{type(exc).__name__}: {exc}'}
        else:return _news_api_cache.get('data') or {'items':[],'clusters':[],
           'loading':True,'note':'新闻独立获取中，行情与选股不等待'}
    with _news_api_lock:_news_api_cache.update(data=data,ts=time.time())
    return data

@app.get('/api/news-live')
def news_live():
    return JSONResponse(_get_cached_news())

@app.get('/api/dragon-tiger')
def dragon_tiger(trade_date:str|None=None):
    # Cache on server (one request per trading date every 10 minutes max).
    day=trade_date or now_cn().date().isoformat()
    with _lock:
        cached=_dragon_cache.get(day)
    if cached and time.time()-cached['ts']<600:return JSONResponse(cached['data'])
    data=fetch_dragons(day)
    with _lock:_dragon_cache[day]={'ts':time.time(),'data':data}
    return JSONResponse(data)

@app.post('/api/technical/scan')
def technical_scan(x_control_token:str|None=Header(None)):
    _require_owner(x_control_token)
    return JSONResponse(tech_start_from_source(now_cn().date().isoformat()))

@app.get('/api/technical/results')
def technical_results(trade_date:str|None=None):
    return JSONResponse(tech_results(trade_date or now_cn().date().isoformat()))

@app.post('/api/picks/{kind}/regenerate')
def regenerate(kind:str,x_control_token:str|None=Header(None)):
    _require_owner(x_control_token)
    if kind not in {'review','next5'}:raise HTTPException(404,'unknown pick kind')
    # Erase old freeze only at an explicit owner-triggered action; next GET re-runs the same method.
    day=now_cn().date().isoformat()
    if now_cn().hour<15:raise HTTPException(409,'请在交易日15:00后重新生成收盘结果')
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        c.execute('DELETE FROM frozen_picks WHERE trade_date=? AND kind=?',(day,kind))
    return {'ok':True,'date':day,'kind':kind,'note':'已清除当日冻结结果；下次请求将重新生成'}

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
        "version": "6.11.1-nonblocking-review",
        "time": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "cache_seconds": CACHE_SECONDS,
        "db": {"ok": db_ok, "path": str(DB_PATH), "error": db_error},
        "refresh": _refresh_meta(),
        "close_review": {"running": bool(_close_review_state["running"]), "last_error": _close_review_state["error"], "attempt": _close_review_state["attempt"], "has_cache": _close_review_cache["data"] is not None},
    }
