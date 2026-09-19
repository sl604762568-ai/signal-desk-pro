from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

HOTSPOT_URL = os.getenv("HOTSPOT_DESK_URL", "https://hotspot-link-desk.sl604762568.chatgpt.site")
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/123 Safari/537.36", "Accept": "text/html,application/xhtml+xml"}


def fetch_hotspot_desk() -> Dict[str, Any]:
    result: Dict[str, Any] = {"url": HOTSPOT_URL, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "signals": [], "candidates": [], "error": None}
    try:
        r = requests.get(HOTSPOT_URL, headers=HEADERS, timeout=6)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        # 页面现有结构中，事件标题主要位于 h2；过滤功能性标题。
        skip = {"利润传导链", "核心判断", "候选个股", "反向验证", "市场验证", "来源状态"}
        signals: List[Dict[str, Any]] = []
        for h in soup.find_all(["h1", "h2", "h3"]):
            t = " ".join(h.get_text(" ", strip=True).split())
            if not t or t in skip or "从新闻事实" in t or len(t) < 8:
                continue
            if any(x["title"] == t for x in signals):
                continue
            signals.append({"title": t})
        # 若语义 HTML 不稳定，使用已知段落模式兜底抽取包含“公开/达成/概率/声明”等事件句。
        if not signals:
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            for line in lines:
                if len(line) >= 12 and any(k in line for k in ["公开", "达成", "概率", "声明", "发布", "启动", "签署"]):
                    signals.append({"title": line[:90]})
        # 抽取 A 股 6 位代码及其前置公司名。
        seen = set()
        candidates = []
        for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z·]{2,12})\s*([0368]\d{5})", text):
            name, code = m.group(1), m.group(2)
            key = (name, code)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"name": name, "code": code})
        result["signals"] = signals[:12]
        result["candidates"] = candidates[:30]
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
