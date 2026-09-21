from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def stage_from_score(score: float) -> str:
    if score < 20:
        return "冰点"
    if score < 35:
        return "修复"
    if score < 50:
        return "升温"
    if score < 68:
        return "主升"
    if score < 82:
        return "高潮"
    if score < 90:
        return "强高潮"
    return "过热"


def score_sentiment(metrics: Dict[str, Any]) -> Dict[str, Any]:
    zt_raw = metrics.get("zt_count")
    dt_raw = metrics.get("dt_count")
    seal_raw = metrics.get("seal_rate")
    limits_known = zt_raw is not None and dt_raw is not None and seal_raw is not None
    zt = float(zt_raw or 0)
    dt = float(dt_raw or 0)
    seal = float(seal_raw or 0)
    premium_raw = metrics.get("yesterday_premium")
    premium = float(premium_raw) if premium_raw is not None else 0.0
    max_board = float(metrics.get("max_board", 0) or 0)
    up_raw = metrics.get("up_count")
    down_raw = metrics.get("down_count")
    breadth_known = up_raw is not None and down_raw is not None
    up = float(up_raw or 0)
    down = float(down_raw or 0)
    promotions = metrics.get("promotion_rates", []) or []

    limit_score = clamp(zt / 85 * 100) if limits_known else 50.0
    seal_score = clamp(seal) if limits_known else 50.0
    premium_score = clamp((premium + 5) / 10 * 100) if premium_raw is not None else 50.0
    breadth_score = clamp(up / max(1.0, up + down) * 100) if breadth_known else 50.0
    high_score = clamp(max_board / 8 * 100)
    safety_score = 100 - clamp(dt / 35 * 100) if limits_known else 50.0

    valid_rates = [float(x.get("rate", 0) or 0) for x in promotions if x.get("denominator", 0)]
    if valid_rates:
        # 晋级率 60% 已经很强；映射到 100 分，减少小样本直接“满分”的影响。
        promo_score = sum(clamp(r / 60 * 100) for r in valid_rates[:4]) / min(4, len(valid_rates))
    else:
        promo_score = 50.0

    score = (
        limit_score * 0.18
        + seal_score * 0.15
        + premium_score * 0.17
        + promo_score * 0.18
        + breadth_score * 0.12
        + high_score * 0.12
        + safety_score * 0.08
    )
    score = round(clamp(score), 1)

    # 比“分数映射”更贴近超短语言的修正：明显亏钱效应时，强制降档。
    stage = stage_from_score(score)
    if (limits_known and dt >= 25) or premium <= -3:
        stage = "退潮"
    elif score < 28 and ((limits_known and dt >= 12) or (limits_known and seal < 55)):
        stage = "冰点"
    elif score >= 65 and limits_known and seal < 62:
        stage = "分歧"

    return {
        "score": score,
        "stage": stage,
        "components": {
            "涨停强度": round(limit_score, 1),
            "封板质量": round(seal_score, 1),
            "昨日反馈": round(premium_score, 1),
            "连板接力": round(promo_score, 1),
            "市场广度": round(breadth_score, 1),
            "空间高度": round(high_score, 1),
            "亏钱抑制": round(safety_score, 1),
        },
    }


def build_alerts(metrics: Dict[str, Any], score_info: Dict[str, Any]) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []
    seal_raw = metrics.get("seal_rate")
    dt_raw = metrics.get("dt_count")
    limits_known = seal_raw is not None and dt_raw is not None and metrics.get("zt_count") is not None
    seal = float(seal_raw or 0)
    dt = int(dt_raw or 0)
    premium_raw = metrics.get("yesterday_premium")
    premium = float(premium_raw) if premium_raw is not None else 0.0
    max_board = int(metrics.get("max_board", 0) or 0)
    auction_gap = metrics.get("auction", {}).get("avg_gap")

    if not limits_known:
        alerts.append({"level":"warn","title":"全市场统计暂不可用","text":"当前仅取得部分实时股票，涨跌停/封板率不参与判断，避免样本池误导。"})
    elif seal < 58:
        alerts.append({"level": "danger", "title": "炸板压力偏高", "text": f"当前封板率 {seal:.1f}%，短线承接偏弱。"})
    elif seal >= 75:
        alerts.append({"level": "good", "title": "封板质量较强", "text": f"当前封板率 {seal:.1f}%，封板稳定性较好。"})
    if limits_known and dt >= 20:
        alerts.append({"level": "danger", "title": "亏钱效应扩散", "text": f"跌停 {dt} 家，注意高位负反馈。"})
    if premium_raw is not None and premium <= -2:
        alerts.append({"level": "warn", "title": "昨日涨停反馈偏弱", "text": f"昨日涨停平均反馈 {premium:.2f}%。"})
    elif premium_raw is not None and premium >= 2:
        alerts.append({"level": "good", "title": "昨日涨停有溢价", "text": f"昨日涨停平均反馈 +{premium:.2f}%。"})
    if max_board >= 6:
        alerts.append({"level": "good", "title": "市场高度打开", "text": f"当前最高 {max_board} 板，空间标高度较高。"})
    if auction_gap is not None:
        if auction_gap >= 1.5:
            alerts.append({"level": "good", "title": "昨日涨停竞价偏强", "text": f"平均开盘缺口 +{auction_gap:.2f}%。"})
        elif auction_gap <= -1.5:
            alerts.append({"level": "warn", "title": "昨日涨停竞价偏弱", "text": f"平均开盘缺口 {auction_gap:.2f}%。"})
    if not alerts:
        alerts.append({"level": "neutral", "title": "盘面中性", "text": f"综合情绪 {score_info['score']}，暂未触发明显极端信号。"})
    return alerts[:5]


def build_review(metrics: Dict[str, Any], score_info: Dict[str, Any]) -> Dict[str, str]:
    score = score_info["score"]
    stage = score_info["stage"]
    zt = metrics.get("zt_count")
    dt = metrics.get("dt_count")
    seal = metrics.get("seal_rate")
    limits_known = zt is not None and dt is not None and seal is not None
    premium = metrics.get("yesterday_premium")
    max_board = metrics.get("max_board", 0)
    promo = metrics.get("promotion_rates", [])
    best_promo = max((p.get("rate", 0) for p in promo if p.get("denominator", 0)), default=0)

    if premium is not None and premium >= 1.5 and limits_known and seal >= 70:
        money = "昨日涨停有正溢价，封板质量也较好，赚钱效应偏正。"
    elif (premium is not None and premium < 0) or (limits_known and seal < 60):
        money = "昨日涨停反馈或封板质量偏弱，短线亏钱效应需要防范。"
    else:
        money = "赚钱效应处于中性区，强弱分化仍然明显。"

    relay = f"最高板 {max_board} 板，最高一档晋级率约 {best_promo:.1f}%。" if promo else f"最高板 {max_board} 板。"
    focus = "重点观察高标反馈、2进3/3进4、炸板率，以及主线板块是否继续集中。"
    return {
        "headline": f"情绪 {score} · {stage}",
        "market": (f"涨停 {zt} 家、跌停 {dt} 家、封板率 {seal:.1f}%。" if limits_known else "当前为部分行情覆盖，涨跌停/封板率暂不输出，避免误判。"),
        "money": money,
        "relay": relay,
        "focus": focus,
    }
