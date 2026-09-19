from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
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

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DB_PATH = Path(os.getenv("DB_PATH", BASE / "sentiment.db"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "75"))
CN_TZ = ZoneInfo("Asia/Shanghai")

app = FastAPI(title="热点链路 × A股短线量价工作台", version="6.1-review-chan")
app.add_middleware(GZipMiddleware, minimum_size=700)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "mode": None}
_lock = threading.Lock()


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


def build_dashboard(force_demo: bool=False) -> Dict[str, Any]:
    provider=get_provider(force_demo=force_demo)
    market_error=None
    try:
        market=provider.fetch()
    except Exception as exc:
        market_error=f"实时行情失败：{type(exc).__name__}: {exc}"
        provider=get_provider(force_demo=True)
        market=provider.fetch(); market["source"]="行情演示回退"; market["is_live"]=False

    market["sentiment"]=score_sentiment(market)
    market["alerts"]=build_alerts(market,market["sentiment"])
    market["review"]=build_review(market,market["sentiment"])
    market["error"]=market_error
    save_snapshot(market)
    market["history"]=load_history(20)

    if force_demo:
        news=demo_news_radar()
    else:
        news=build_news_radar()
        if not news.get("items"):
            fallback=demo_news_radar(); fallback["errors"]=(news.get("errors") or [])+["所有实时新闻源暂无可用数据，已显示演示结构"]
            news=fallback

    hotspot=fetch_hotspot_desk() if not force_demo else {"url":os.getenv("HOTSPOT_DESK_URL","https://hotspot-link-desk.sl604762568.chatgpt.site"),"signals":[],"candidates":[],"error":"演示模式未抓取外部热点链路"}
    ak=getattr(provider,"ak",None) if market.get("is_live") else None
    candidates=build_candidates(market,news,hotspot,ak=ak,limit=12)

    payload={**market,"news":news,"hotspot":hotspot,"candidates":candidates,"model":{
        "name":"热点×情绪×量价个股研究模型 v6.1","weights":{"量价":40,"热点新闻":25,"市场情绪":20,"强势结构":15},
        "note":"评分代表研究优先度，不预测涨跌，不构成交易指令。"
    }}
    return payload


@app.on_event("startup")
def _startup(): init_db()

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
    now=time.time(); key=mode
    with _lock:
        if not fresh and _cache.get("data") is not None and _cache.get("mode")==key and now-float(_cache.get("ts",0))<CACHE_SECONDS:
            return JSONResponse(_cache["data"])
        data=build_dashboard(force_demo=(mode=="demo")); _cache.update({"ts":now,"data":data,"mode":key})
        return JSONResponse(data)

@app.get("/api/review-picks")
def review_picks(mode: str=Query("auto",pattern="^(auto|demo)$"), limit: int=Query(10,ge=3,le=20)):
    # 复用dashboard行情/新闻；扫描历史日线按按钮触发，避免首页每次加载都产生大量请求。
    data=build_dashboard(force_demo=(mode=="demo"))
    if not data.get("is_live"):
        return JSONResponse({"regime": {"level":"--","score":0,"note":"实时行情不可用"}, "picks":[], "chan_picks":[], "scanned":0, "error":"真实行情源暂不可用，复盘选股不输出虚构个股。"})
    try:
        import akshare as ak
        result=build_review_picks(data, data.get("news") or {}, ak=ak, limit=limit)
        result["trade_date"]=data.get("trade_date")
        result["updated_at"]=datetime.now(CN_TZ).isoformat(timespec="seconds")
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"regime":{},"picks":[],"chan_picks":[],"scanned":0,"error":f"复盘选股失败：{type(exc).__name__}: {exc}"})

@app.get("/api/stock/{code}")
def stock_detail(code: str):
    code=''.join(ch for ch in code if ch.isdigit())[:6].zfill(6)
    try:
        import akshare as ak
        end=datetime.now(CN_TZ).strftime("%Y%m%d"); start=(datetime.now(CN_TZ)-timedelta(days=120)).strftime("%Y%m%d")
        df=ak.stock_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end,adjust="qfq")
        if df is None or df.empty: return JSONResponse({"code":code,"rows":[],"error":"暂无K线"})
        rows=[]
        for _,r in df.tail(70).iterrows():
            rows.append({"date":str(r.get("日期")),"open":float(r.get("开盘",0)),"close":float(r.get("收盘",0)),"high":float(r.get("最高",0)),"low":float(r.get("最低",0)),"volume":float(r.get("成交量",0)),"amount":float(r.get("成交额",0))})
        return {"code":code,"rows":rows,"error":None}
    except Exception as exc:
        return JSONResponse({"code":code,"rows":[],"error":f"{type(exc).__name__}: {exc}"})

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
        "version": "6.1-review-chan",
        "time": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "cache_seconds": CACHE_SECONDS,
        "db": {"ok": db_ok, "path": str(DB_PATH), "error": db_error},
    }
