"""Compute liquidation pressure scores (Phase 4 offensive module).

The module ingests the raw snapshot created by
`scripts/fetch_binance_leverage_data.py` and converts it into two pressure
scores:

* `L_long_liq`: probability-weighted risk that leveraged longs are close to
  liquidation (full bar ⇒ prefer SHORT setups).
* `L_short_liq`: symmetric view for shorts (full bar ⇒ prefer LONG setups).

These scores are later consumed by the paper trading system to boost offensive
modes when the market is crowded on one side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class PressureLevel(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class LiquidationPressureSnapshot:
    symbol: str
    collected_at: str
    long_score: float
    short_score: float
    long_level: PressureLevel
    short_level: PressureLevel
    directional_bias: str
    bias_confidence: float
    summary: str
    annotations: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "collected_at": self.collected_at,
            "long_score": round(self.long_score, 2),
            "short_score": round(self.short_score, 2),
            "long_level": self.long_level.value,
            "short_level": self.short_level.value,
            "directional_bias": self.directional_bias,
            "bias_confidence": round(self.bias_confidence, 3),
            "summary": self.summary,
            "annotations": self.annotations,
        }


# ------------------------ Math helpers ------------------------ #

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _level_from_score(score: float) -> PressureLevel:
    if score >= 85:
        return PressureLevel.EXTREME
    if score >= 65:
        return PressureLevel.HIGH
    if score >= 45:
        return PressureLevel.MEDIUM
    if score >= 25:
        return PressureLevel.LOW
    return PressureLevel.VERY_LOW


def _bias_from_scores(long_score: float, short_score: float) -> Tuple[str, float]:
    diff = abs(long_score - short_score)
    dominant = "SHORT" if long_score > short_score else "LONG"

    if long_score >= 65 and short_score >= 65:
        return "BOTH_HIGH", diff / 100
    if long_score <= 25 and short_score <= 25:
        return "NEUTRAL", diff / 100
    if diff < 10:
        return "BALANCED", diff / 100
    if dominant == "SHORT":
        return "SHORT", diff / 100
    return "LONG", diff / 100


def _bar(score: float, width: int = 10) -> str:
    filled = int(round(_clamp(score / 100, 0.0, 1.0) * width))
    return "█" * filled + "-" * (width - filled)


def _summarize(long_score: float, short_score: float) -> str:
    level_long = _level_from_score(long_score)
    level_short = _level_from_score(short_score)
    bias, _ = _bias_from_scores(long_score, short_score)

    if bias == "SHORT":
        return f"多頭爆倉壓力 {level_long.value}，做空優勢增加"
    if bias == "LONG":
        return f"空頭爆倉壓力 {level_short.value}，做多優勢增加"
    if bias == "BOTH_HIGH":
        return "多空都極度擁擠，容易雙向急殺急拉"
    if bias == "NEUTRAL":
        return "爆倉壓力很低，訊號僅供參考"
    return "多空壓力接近，需配合其他指標"


# ------------------------ Core computation ------------------------ #

def compute_liquidation_pressure(raw_snapshot: Dict[str, Any]) -> Optional[LiquidationPressureSnapshot]:
    """Convert the Binance snapshot into long/short liquidation scores."""

    if not raw_snapshot:
        return None

    global_ratio_series = raw_snapshot.get("global_long_short") or []
    top_ratio_series = raw_snapshot.get("top_long_short") or []
    oi_series = raw_snapshot.get("open_interest") or []
    funding_series = raw_snapshot.get("funding_rate") or []
    force_orders = raw_snapshot.get("force_orders") or []
    # 🆕 新增數據源
    taker_ratio_series = raw_snapshot.get("taker_long_short") or []
    top_position_series = raw_snapshot.get("top_position_ratio") or []

    if not global_ratio_series or not top_ratio_series or not oi_series:
        return None

    last_global = global_ratio_series[-1]
    last_top = top_ratio_series[-1]
    first_oi = oi_series[0]
    last_oi = oi_series[-1]

    global_ratio = _safe_float(last_global.get("longShortRatio"), 1.0)
    top_ratio = _safe_float(last_top.get("longShortRatio"), 1.0)

    funding_rate = 0.0
    if funding_series:
        funding_rate = _safe_float(funding_series[-1].get("fundingRate"), 0.0)

    # 🆕 Taker Buy/Sell Ratio - 爆倉偵測的關鍵指標
    taker_buy_sell_ratio = 1.0
    taker_buy_vol = 0.0
    taker_sell_vol = 0.0
    if taker_ratio_series:
        last_taker = taker_ratio_series[-1]
        taker_buy_sell_ratio = _safe_float(last_taker.get("buySellRatio"), 1.0)
        taker_buy_vol = _safe_float(last_taker.get("buyVol"), 0.0)
        taker_sell_vol = _safe_float(last_taker.get("sellVol"), 0.0)
    
    # 🆕 頂級交易員持倉比 - 跟散戶反著做的參考
    top_position_ratio = 1.0
    if top_position_series:
        last_top_pos = top_position_series[-1]
        top_position_ratio = _safe_float(last_top_pos.get("longShortRatio"), 1.0)

    oi_start = _safe_float(first_oi.get("sumOpenInterest"), 0.0)
    oi_end = _safe_float(last_oi.get("sumOpenInterest"), oi_start)
    oi_change_pct = 0.0
    if oi_start > 0:
        oi_change_pct = (oi_end - oi_start) / oi_start

    # -------------------------------------------------------------------------
    # 🆕 Force Orders / Liquidation Estimation Logic
    # -------------------------------------------------------------------------
    # Try to use real force orders first
    sell_force_usd = 0.0
    buy_force_usd = 0.0
    real_force_orders_found = False

    if force_orders:
        real_force_orders_found = True
        for order in force_orders:
            qty = _safe_float(order.get("executedQty") or order.get("origQty"))
            price = _safe_float(order.get("averagePrice") or order.get("price"))
            usd = qty * price
            side = (order.get("side") or "").upper()
            if side == "SELL":
                sell_force_usd += usd
            elif side == "BUY":
                buy_force_usd += usd

    # If no real force orders (API 401 or empty), estimate from OI & Price changes
    # This is critical because without it, the pressure score is capped at ~40
    if not real_force_orders_found and len(oi_series) >= 2:
        simulated_sell_force = 0.0
        simulated_buy_force = 0.0
        
        for i in range(1, len(oi_series)):
            curr = oi_series[i]
            prev = oi_series[i-1]
            
            # Extract OI and Price
            curr_oi = _safe_float(curr.get("sumOpenInterest"))
            prev_oi = _safe_float(prev.get("sumOpenInterest"))
            
            curr_val = _safe_float(curr.get("sumOpenInterestValue"))
            prev_val = _safe_float(prev.get("sumOpenInterestValue"))
            
            # Calculate Price (Value / OI)
            curr_price = curr_val / curr_oi if curr_oi > 0 else 0
            prev_price = prev_val / prev_oi if prev_oi > 0 else 0
            
            if curr_price == 0 or prev_price == 0:
                continue
                
            delta_oi = curr_oi - prev_oi
            price_change_pct = (curr_price - prev_price) / prev_price
            
            # Logic: 
            # OI Drop + Price Drop = Long Liquidation (Sell Force)
            # OI Drop + Price Rise = Short Liquidation (Buy Force)
            if delta_oi < 0:
                delta_usd = abs(delta_oi) * curr_price
                if price_change_pct < -0.0005:  # Price dropped > 0.05%
                    simulated_sell_force += delta_usd
                elif price_change_pct > 0.0005: # Price rose > 0.05%
                    simulated_buy_force += delta_usd
        
        # Apply a factor because not all OI drop is liquidation (some is voluntary closing)
        # But in high volatility, it's a good proxy for pressure release
        ESTIMATION_FACTOR = 0.5
        sell_force_usd = simulated_sell_force * ESTIMATION_FACTOR
        buy_force_usd = simulated_buy_force * ESTIMATION_FACTOR

    total_force = sell_force_usd + buy_force_usd
    sell_share = (sell_force_usd / total_force) if total_force > 0 else 0.0
    buy_share = (buy_force_usd / total_force) if total_force > 0 else 0.0

    BASE_FORCE_USD = 4_000_000  # heuristic scaling

    # 🆕 Taker Ratio 分析
    # buySellRatio > 1.2 = 主動買入強勢 → 空頭可能被軋
    # buySellRatio < 0.8 = 主動賣出強勢 → 多頭可能被爆
    taker_long_pressure = _clamp((1.0 / taker_buy_sell_ratio - 0.8) / 0.7) if taker_buy_sell_ratio > 0 else 0.0
    taker_short_pressure = _clamp((taker_buy_sell_ratio - 0.8) / 0.7)
    
    # 🆕 OI 速度變化 (Rate of Change)
    oi_roc = 0.0
    if len(oi_series) >= 5:
        recent_oi = _safe_float(oi_series[-1].get("sumOpenInterest"), 0.0)
        past_oi = _safe_float(oi_series[-5].get("sumOpenInterest"), recent_oi)
        if past_oi > 0:
            oi_roc = (recent_oi - past_oi) / past_oi  # 最近 5 根 K 線的 OI 變化率

    long_components = {
        "crowding": _clamp((global_ratio - 1.0) / 1.2),
        "top_crowding": _clamp((top_ratio - 1.0) / 1.1),
        "funding": _clamp(max(funding_rate, 0.0) / 0.015),
        "oi_trend": _clamp(max(oi_change_pct, 0.0) / 0.25),
        "force_share": _clamp(sell_share / 0.7),
        "force_volume": _clamp(sell_force_usd / BASE_FORCE_USD),
        # 🆕 新增成分
        "taker_pressure": taker_long_pressure,
        "oi_velocity": _clamp(max(-oi_roc, 0.0) / 0.02),  # OI 快速下降 = 爆倉中
    }
    short_components = {
        "crowding": _clamp(((1 / global_ratio) - 1.0) / 1.2) if global_ratio > 0 else 0.0,
        "top_crowding": _clamp(((1 / top_ratio) - 1.0) / 1.1) if top_ratio > 0 else 0.0,
        "funding": _clamp(max(-funding_rate, 0.0) / 0.015),
        "oi_trend": _clamp(max(oi_change_pct, 0.0) / 0.25) if global_ratio < 1 else _clamp(max(-oi_change_pct, 0.0) / 0.25),
        "force_share": _clamp(buy_share / 0.7),
        "force_volume": _clamp(buy_force_usd / BASE_FORCE_USD),
        # 🆕 新增成分
        "taker_pressure": taker_short_pressure,
        "oi_velocity": _clamp(max(-oi_roc, 0.0) / 0.02),
    }

    def combine(components: Dict[str, float]) -> float:
        # 🆕 更新權重：加入 taker_pressure 和 oi_velocity
        weights = {
            "crowding": 18,         # 原 22 → 18
            "top_crowding": 15,     # 原 18 → 15
            "funding": 15,          # 原 18 → 15
            "oi_trend": 12,         # 原 15 → 12
            "force_share": 10,      # 原 12 → 10
            "force_volume": 12,     # 原 15 → 12
            # 🆕 新增權重
            "taker_pressure": 10,   # Taker 買賣比
            "oi_velocity": 8,       # OI 變化速度
        }
        total = 0.0
        for key, weight in weights.items():
            total += components.get(key, 0.0) * weight
        return _clamp(total, 0.0, 100.0)

    long_score = combine(long_components)
    short_score = combine(short_components)

    long_level = _level_from_score(long_score)
    short_level = _level_from_score(short_score)
    bias, bias_conf = _bias_from_scores(long_score, short_score)
    summary = _summarize(long_score, short_score)

    annotations = {
        "global_ratio": global_ratio,
        "top_ratio": top_ratio,
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change_pct,
        "sell_force_usd": sell_force_usd,
        "buy_force_usd": buy_force_usd,
        "long_bar": _bar(long_score),
        "short_bar": _bar(short_score),
        "long_components": long_components,
        "short_components": short_components,
        # 🆕 新增數據
        "taker_buy_sell_ratio": taker_buy_sell_ratio,
        "taker_buy_vol": taker_buy_vol,
        "taker_sell_vol": taker_sell_vol,
        "top_position_ratio": top_position_ratio,
        "oi_velocity": oi_roc,
    }

    return LiquidationPressureSnapshot(
        symbol=str(raw_snapshot.get("symbol", "BTCUSDT")),
        collected_at=str(raw_snapshot.get("collected_at", "")),
        long_score=long_score,
        short_score=short_score,
        long_level=long_level,
        short_level=short_level,
        directional_bias=bias,
        bias_confidence=bias_conf,
        summary=summary,
        annotations=annotations,
    )


# ------------------------ Persistence helpers ------------------------ #

def load_snapshot_from_file(path: str | Path) -> Optional[LiquidationPressureSnapshot]:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return compute_liquidation_pressure(data)


def render_panel(snapshot: LiquidationPressureSnapshot) -> str:
    """Return a console-ready block matching the documentation design."""

    lines = ["💣 爆倉壓力雷達 (Liquidation Pressure)"]
    lines.append(
        "🐂 多頭爆倉壓力 L_long_liq : "
        f"[{snapshot.annotations['long_bar']}] {snapshot.long_score:>5.1f} "
        f"{_level_label(snapshot.long_level)}"
    )
    lines.append(
        "🐻 空頭爆倉壓力 L_short_liq: "
        f"[{snapshot.annotations['short_bar']}] {snapshot.short_score:>5.1f} "
        f"{_level_label(snapshot.short_level)}"
    )

    # 🆕 新增 OI 變化顯示
    oi_change = snapshot.annotations.get('oi_change_pct', 0.0) * 100
    oi_emoji = "🔥" if abs(oi_change) > 0.5 else "💤"
    lines.append(f"📊 持倉量變化 (OI Change): {oi_emoji} {oi_change:+.3f}% (近30根K線)")
    
    # 🆕 Taker Buy/Sell Ratio - 超重要指標
    taker_ratio = snapshot.annotations.get('taker_buy_sell_ratio', 1.0)
    if taker_ratio > 1.2:
        taker_emoji = "🟢"
        taker_text = "買方主導 (軋空風險↑)"
    elif taker_ratio < 0.8:
        taker_emoji = "🔴"
        taker_text = "賣方主導 (爆多風險↑)"
    else:
        taker_emoji = "⚪"
        taker_text = "平衡"
    lines.append(f"⚡ 主動買賣比 (Taker): {taker_emoji} {taker_ratio:.2f} {taker_text}")
    
    # 🆕 OI 變化速度 (爆倉即時指標)
    oi_velocity = snapshot.annotations.get('oi_velocity', 0.0) * 100
    if abs(oi_velocity) > 0.3:
        velocity_emoji = "💥" if oi_velocity < 0 else "📈"
        lines.append(f"🚀 OI 變化速度: {velocity_emoji} {oi_velocity:+.2f}% (近期)")

    interpretation, bias_text = _panel_text(snapshot)
    lines.append(f"➡ 解讀：{interpretation}")
    lines.append(f"➡ 策略傾向：{bias_text}")
    return "\n".join(lines)


def _level_label(level: PressureLevel) -> str:
    return {
        PressureLevel.EXTREME: "🔴 極高",
        PressureLevel.HIGH: "🟠 偏高",
        PressureLevel.MEDIUM: "⚪ 中等",
        PressureLevel.LOW: "🟢 偏低",
        PressureLevel.VERY_LOW: "🟢 很低",
    }[level]


def _panel_text(snapshot: LiquidationPressureSnapshot) -> Tuple[str, str]:
    bias = snapshot.directional_bias
    long_level = snapshot.long_level
    short_level = snapshot.short_level

    if bias == "SHORT":
        interpretation = "多頭槓桿非常擁擠，往下一段容易連環爆" if long_level in {PressureLevel.HIGH, PressureLevel.EXTREME} else "多頭壓力偏高"
        bias_text = "⚠️ 慎追多 ／ ✅ 優先找做空機會"
    elif bias == "LONG":
        interpretation = "空頭槓桿非常擁擠，往上一段容易軋空" if short_level in {PressureLevel.HIGH, PressureLevel.EXTREME} else "空頭壓力偏高"
        bias_text = "⚠️ 慎追空 ／ ✅ 優先找做多機會"
    elif bias == "BOTH_HIGH":
        interpretation = "多空兩邊都塞滿槓桿，超容易急殺急拉"
        bias_text = "⚠️ 慎用高槓桿，只讓最佳策略出手"
    elif bias == "NEUTRAL":
        interpretation = "多空槓桿都很低，爆倉壓力弱"
        bias_text = "⚪ 訊號偏弱，僅當背景資訊"
    else:
        interpretation = "多空壓力接近，需看其他指標"
        bias_text = "⚪ 以 VPIN / OBI / 趨勢為主"
    return interpretation, bias_text
