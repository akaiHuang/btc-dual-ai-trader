#!/usr/bin/env python3
"""
dYdX Random Entry Trader (Lock-Exit Focus)
=========================================

目標：獨立交易腳本（可紙上/可真實），專注你指定的進出場規則。

📥 Entry
- random_entry_mode: True（隨機進場）
- 多空隨機平衡：交替 LONG/SHORT（避免單邊偏差）
- 槓桿：50X（用於 PnL% ↔ 價格換算）
- 部位大小：$100 USDT（名義倉位，不是保證金）

📤 Exit（優先序）
1) 中間數鎖利 Midpoint Lock：止損線 = 最高獲利 × ratio（預設 0.7）
2) N% 鎖 N%：達到門檻後，以 floor(max_profit) - buffer 作為鎖利線
3) 階段性鎖利表 profit_lock_stages（用 max_profit 落點決定 stop line）
4) 固定止盈止損 + 最長持倉時間

⚠️ 風險提示：50X 高槓桿非常危險；預設用紙上模式，需加 --live 才會真正下單。
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional


# 盡量沿用既有 dYdX 下單/取消單封裝
try:
    from dydx_whale_trader import DydxAPI, DydxConfig, DYDX_AVAILABLE  # scripts/ 目錄直接執行時可用
except Exception:  # pragma: no cover
    from scripts.dydx_whale_trader import DydxAPI, DydxConfig, DYDX_AVAILABLE


Direction = Literal["LONG", "SHORT"]


@dataclass
class StrategyConfig:
    # === dYdX ===
    network: str = "testnet"
    symbol: str = "BTC-USD"

    # === Entry ===
    random_entry_mode: bool = True
    leverage: int = 50
    position_size_usdt: float = 100.0  # 名義價值（notional）
    min_seconds_between_trades: float = 3.0

    # === Exit priority 1: Midpoint lock ===
    use_midpoint_lock: bool = True
    midpoint_ratio: float = 0.7

    # === Exit priority 2: N-lock-N ===
    use_n_lock_n: bool = True
    n_lock_n_threshold: float = 0.75
    n_lock_n_buffer: float = 0.25

    # === Exit priority 3: stage table ===
    profit_lock_stages: list[tuple[float, float, float]] = field(
        default_factory=lambda: [
            (-999.0, 0.0, -0.75),
            (0.0, 0.50, -0.75),
            (0.50, 1.00, 0.0),
            (1.00, 1.50, 0.35),
            (1.50, 2.00, 0.70),
            (2.00, 999.0, 1.05),
        ]
    )

    # === Exit priority 4: hard guards ===
    target_profit_pct: float = 2.50  # PnL%
    stop_loss_pct: float = 0.75      # PnL%（表示 -0.75%）
    max_hold_minutes: float = 5.0

    # === Runtime ===
    poll_seconds: float = 1.0
    order_ttl_seconds: int = 3600
    runtime_minutes: Optional[float] = None  # None=無限跑

    # === Live trading ===
    live: bool = False
    use_exchange_brackets: bool = True  # live 時是否掛 TP/SL
    sl_update_min_delta_pct: float = 0.05  # 止損線至少提升 0.05% 才更新（減少 API spam）


@dataclass
class PositionState:
    side: Direction
    size_btc: float
    entry_price: float
    entry_time: float
    max_profit_pct: float = 0.0
    last_stop_pnl_pct: Optional[float] = None

    @property
    def hold_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60.0


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def pnl_pct_from_price(side: Direction, entry_price: float, current_price: float, leverage: int) -> float:
    if entry_price <= 0:
        return 0.0
    if side == "LONG":
        price_move_pct = (current_price - entry_price) / entry_price * 100.0
    else:
        price_move_pct = (entry_price - current_price) / entry_price * 100.0
    return price_move_pct * float(leverage)


def pnl_pct_to_price(side: Direction, entry_price: float, pnl_pct: float, leverage: int) -> float:
    """把「槓桿後 PnL%」換算成對應的價格（以 entry_price 為基準）。"""
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    move = pnl_pct / (100.0 * float(leverage))
    if side == "LONG":
        return entry_price * (1.0 + move)
    return entry_price * (1.0 - move)


def compute_midpoint_lock_stop(max_profit_pct: float, ratio: float) -> Optional[float]:
    if max_profit_pct <= 0:
        return None
    ratio = max(0.0, min(float(ratio), 1.0))
    return max_profit_pct * ratio


def compute_n_lock_n_stop(max_profit_pct: float, threshold: float, buffer: float) -> Optional[float]:
    if max_profit_pct < float(threshold):
        return None
    # 例：max=1.5 -> floor=1 -> stop=1-0.25=0.75
    base = math.floor(max_profit_pct) - float(buffer)
    # 例：threshold=0.75 -> 最低鎖利線 0.75-0.25=0.50
    min_lock = float(threshold) - float(buffer)
    return max(base, min_lock)


def compute_stage_stop(max_profit_pct: float, stages: list[tuple[float, float, float]]) -> Optional[float]:
    for lo, hi, stop in stages:
        if max_profit_pct >= lo and max_profit_pct < hi:
            return float(stop)
    return None


def evaluate_exit(
    cfg: StrategyConfig,
    pos: PositionState,
    net_pnl_pct: float,
) -> tuple[Optional[str], dict]:
    """依照你指定的優先序回傳 (reason, details)。"""
    details: dict = {
        "pnl_pct": net_pnl_pct,
        "max_profit_pct": pos.max_profit_pct,
        "hold_minutes": pos.hold_minutes,
    }

    # 1) Midpoint lock (最優先)
    if cfg.use_midpoint_lock:
        stop = compute_midpoint_lock_stop(pos.max_profit_pct, cfg.midpoint_ratio)
        if stop is not None:
            details["midpoint_stop_pct"] = stop
            if net_pnl_pct <= stop:
                return "MIDPOINT_LOCK", details

    # 2) N-lock-N
    if cfg.use_n_lock_n:
        stop = compute_n_lock_n_stop(pos.max_profit_pct, cfg.n_lock_n_threshold, cfg.n_lock_n_buffer)
        if stop is not None:
            details["n_lock_n_stop_pct"] = stop
            if net_pnl_pct <= stop:
                return "N_LOCK_N", details

    # 3) profit_lock_stages
    if cfg.profit_lock_stages:
        stop = compute_stage_stop(pos.max_profit_pct, cfg.profit_lock_stages)
        if stop is not None:
            details["stage_stop_pct"] = stop
            if net_pnl_pct <= stop:
                return "PROFIT_LOCK_STAGE", details

    # 4) 固定 TP/SL/時間
    if net_pnl_pct >= cfg.target_profit_pct:
        details["target_profit_pct"] = cfg.target_profit_pct
        return "TAKE_PROFIT", details

    if net_pnl_pct <= -cfg.stop_loss_pct:
        details["stop_loss_pct"] = cfg.stop_loss_pct
        return "STOP_LOSS", details

    if pos.hold_minutes >= cfg.max_hold_minutes:
        details["max_hold_minutes"] = cfg.max_hold_minutes
        return "MAX_HOLD_TIME", details

    return None, details


def desired_trailing_stop_pnl_pct(cfg: StrategyConfig, pos: PositionState) -> float:
    """用於掛在交易所的動態止損（取所有規則的「最嚴格」止損線）。"""
    candidates: list[float] = [-cfg.stop_loss_pct]

    stage = compute_stage_stop(pos.max_profit_pct, cfg.profit_lock_stages) if cfg.profit_lock_stages else None
    if stage is not None:
        candidates.append(stage)

    if cfg.use_n_lock_n:
        nlock = compute_n_lock_n_stop(pos.max_profit_pct, cfg.n_lock_n_threshold, cfg.n_lock_n_buffer)
        if nlock is not None:
            candidates.append(nlock)

    if cfg.use_midpoint_lock:
        mid = compute_midpoint_lock_stop(pos.max_profit_pct, cfg.midpoint_ratio)
        if mid is not None:
            candidates.append(mid)

    return max(candidates)


class BalancedRandomDirection:
    def __init__(self):
        self.last: Optional[Direction] = None
        self.long_count = 0
        self.short_count = 0

    def next(self) -> Direction:
        # 優先補齊較少的一邊，平衡後再隨機
        if self.long_count < self.short_count:
            self.last = "LONG"
        elif self.short_count < self.long_count:
            self.last = "SHORT"
        else:
            self.last = random.choice(["LONG", "SHORT"])

        if self.last == "LONG":
            self.long_count += 1
        else:
            self.short_count += 1
        return self.last


async def _find_open_position(api: DydxAPI, symbol: str) -> Optional[tuple[Direction, float, float]]:
    """回傳 (side, size_btc, entry_price)"""
    try:
        positions = await api.get_positions()
        for p in positions:
            if p.get("market") != symbol:
                continue
            if p.get("status") != "OPEN":
                continue
            size = float(p.get("size", 0))
            if abs(size) <= 0.00001:
                continue
            side: Direction = "LONG" if size > 0 else "SHORT"
            return side, abs(size), float(p.get("entryPrice", 0))
    except Exception:
        return None
    return None


async def _sweep_orders(api: DydxAPI, symbol: str) -> None:
    # 同時清 OPEN / UNTRIGGERED，避免 TP/SL 殘留開反向倉
    await api.cancel_open_orders(symbol=symbol, status=["OPEN", "UNTRIGGERED"])


async def _place_brackets(api: DydxAPI, cfg: StrategyConfig, pos: PositionState) -> None:
    if not cfg.use_exchange_brackets:
        return

    tp_price = pnl_pct_to_price(pos.side, pos.entry_price, cfg.target_profit_pct, cfg.leverage)
    sl_price = pnl_pct_to_price(pos.side, pos.entry_price, -cfg.stop_loss_pct, cfg.leverage)

    # 先清條件單，避免 SL 更新累積（TP 限價單不在條件單內）
    await api.cancel_all_conditional_orders()
    await api.place_stop_loss_order(
        side=pos.side,
        size=pos.size_btc,
        stop_price=sl_price,
        time_to_live_seconds=cfg.order_ttl_seconds,
    )
    await api.place_take_profit_order(
        side=pos.side,
        size=pos.size_btc,
        tp_price=tp_price,
        time_to_live_seconds=cfg.order_ttl_seconds,
    )

    pos.last_stop_pnl_pct = -cfg.stop_loss_pct


async def _update_stop_if_needed(api: DydxAPI, cfg: StrategyConfig, pos: PositionState, net_pnl_pct: float) -> None:
    if not cfg.use_exchange_brackets:
        return

    desired = desired_trailing_stop_pnl_pct(cfg, pos)

    # 只在「仍然在 desired 之上」時才更新（避免立即觸發）
    if net_pnl_pct <= desired:
        return

    # 減少更新頻率：至少提升 cfg.sl_update_min_delta_pct
    if pos.last_stop_pnl_pct is not None and (desired - pos.last_stop_pnl_pct) < cfg.sl_update_min_delta_pct:
        return

    stop_price = pnl_pct_to_price(pos.side, pos.entry_price, desired, cfg.leverage)
    if stop_price <= 0:
        return

    await api.cancel_all_conditional_orders()
    await api.place_stop_loss_order(
        side=pos.side,
        size=pos.size_btc,
        stop_price=stop_price,
        time_to_live_seconds=cfg.order_ttl_seconds,
    )

    pos.last_stop_pnl_pct = desired


async def run(cfg: StrategyConfig, runtime_minutes: Optional[float]) -> None:
    if not DYDX_AVAILABLE:
        raise SystemExit("❌ dYdX SDK 未安裝，請先安裝: pip install dydx-v4-client")

    dydx_cfg = DydxConfig(
        network=cfg.network,
        symbol=cfg.symbol,
        leverage=cfg.leverage,
        paper_trading=not cfg.live,
    )
    api = DydxAPI(dydx_cfg)

    if not await api.connect():
        raise SystemExit("❌ dYdX 連線失敗")

    direction_picker = BalancedRandomDirection()
    active: Optional[PositionState] = None
    last_trade_ts = 0.0

    if cfg.live:
        await _sweep_orders(api, cfg.symbol)
        existing = await _find_open_position(api, cfg.symbol)
        if existing:
            side, size, entry = existing
            active = PositionState(side=side, size_btc=size, entry_price=entry, entry_time=time.time())
            print(f"[{_utc_ts()}] 📌 接管既有持倉: {side} {size:.6f} BTC @ {entry:,.2f}")
            await _place_brackets(api, cfg, active)

    end_ts = time.time() + runtime_minutes * 60.0 if runtime_minutes else None

    while True:
        if end_ts and time.time() >= end_ts:
            print(f"[{_utc_ts()}] 🛑 Runtime 到期，結束")
            return

        price = await api.get_price()
        if price <= 0:
            await asyncio.sleep(cfg.poll_seconds)
            continue

        # === 無持倉：考慮進場 ===
        if active is None:
            if time.time() - last_trade_ts < cfg.min_seconds_between_trades:
                await asyncio.sleep(cfg.poll_seconds)
                continue

            if not cfg.random_entry_mode:
                await asyncio.sleep(cfg.poll_seconds)
                continue

            side = direction_picker.next()
            size_btc = cfg.position_size_usdt / price
            size_btc = float(f"{size_btc:.6f}")  # 控制精度，避免 subticks/size rounding 問題
            if size_btc <= 0:
                await asyncio.sleep(cfg.poll_seconds)
                continue

            if cfg.live:
                await _sweep_orders(api, cfg.symbol)
                tx_hash, fill_price = await api.place_fast_order(side=side, size=size_btc)
                if not tx_hash or fill_price <= 0:
                    print(f"[{_utc_ts()}] ❌ 進場失敗: {side} size={size_btc}")
                    await asyncio.sleep(cfg.poll_seconds)
                    continue
                entry_price = fill_price
            else:
                entry_price = price

            active = PositionState(
                side=side,
                size_btc=size_btc,
                entry_price=entry_price,
                entry_time=time.time(),
            )
            last_trade_ts = time.time()

            mode = "LIVE" if cfg.live else "PAPER"
            print(
                f"[{_utc_ts()}] 📥 {mode} ENTRY {side} | "
                f"price={entry_price:,.2f} | size={size_btc:.6f} BTC | lev={cfg.leverage}X"
            )

            if cfg.live:
                await _place_brackets(api, cfg, active)

            await asyncio.sleep(cfg.poll_seconds)
            continue

        # === 有持倉：管理出場 ===
        net_pnl_pct = pnl_pct_from_price(active.side, active.entry_price, price, cfg.leverage)
        active.max_profit_pct = max(active.max_profit_pct, net_pnl_pct)

        # 若交易所已經把倉位平掉（TP/SL 觸發），做一次清單與重置
        if cfg.live:
            exch_pos = await _find_open_position(api, cfg.symbol)
            if exch_pos is None:
                print(f"[{_utc_ts()}] ✅ POSITION CLOSED ON-CHAIN | sweeping orders")
                await _sweep_orders(api, cfg.symbol)
                active = None
                last_trade_ts = time.time()
                await asyncio.sleep(cfg.poll_seconds)
                continue

        reason, details = evaluate_exit(cfg, active, net_pnl_pct)
        if reason:
            mode = "LIVE" if cfg.live else "PAPER"
            print(
                f"[{_utc_ts()}] 📤 {mode} EXIT {reason} | pnl={net_pnl_pct:.2f}% | "
                f"max={active.max_profit_pct:.2f}% | hold={active.hold_minutes:.2f}m"
            )

            if cfg.live:
                await _sweep_orders(api, cfg.symbol)
                await api.close_position_aggressive(
                    side=active.side,
                    size=active.size_btc,
                    timeout_seconds=5.0,
                    is_stop_loss=(reason in {"STOP_LOSS", "MAX_HOLD_TIME", "PROFIT_LOCK_STAGE", "MIDPOINT_LOCK", "N_LOCK_N"}),
                )
                await _sweep_orders(api, cfg.symbol)

            active = None
            last_trade_ts = time.time()
            await asyncio.sleep(cfg.poll_seconds)
            continue

        if cfg.live:
            await _update_stop_if_needed(api, cfg, active, net_pnl_pct)

        print(
            f"[{_utc_ts()}] 🟦 HOLD {active.side} | price={price:,.2f} | "
            f"pnl={net_pnl_pct:.2f}% | max={active.max_profit_pct:.2f}% | "
            f"hold={active.hold_minutes:.2f}m"
        )

        await asyncio.sleep(cfg.poll_seconds)


def parse_args() -> StrategyConfig:
    p = argparse.ArgumentParser(description="dYdX random-entry trader (lock-exit priority rules)")
    p.add_argument("--network", default="testnet", choices=["testnet", "mainnet"])
    p.add_argument("--symbol", default="BTC-USD")
    p.add_argument("--live", action="store_true", help="真的下單（預設是 paper）")
    p.add_argument("--runtime-minutes", type=float, default=0.0, help="0=無限跑，直到 Ctrl-C")
    p.add_argument("--poll-seconds", type=float, default=1.0)
    p.add_argument("--size-usdt", type=float, default=100.0)
    p.add_argument("--leverage", type=int, default=50)

    # Exit params (可快速調整)
    p.add_argument("--target-profit-pct", type=float, default=2.50)
    p.add_argument("--stop-loss-pct", type=float, default=0.75)
    p.add_argument("--max-hold-minutes", type=float, default=5.0)
    p.add_argument("--midpoint-ratio", type=float, default=0.7)
    p.add_argument("--n-lock-n-threshold", type=float, default=0.75)
    p.add_argument("--n-lock-n-buffer", type=float, default=0.25)

    args = p.parse_args()

    cfg = StrategyConfig(
        network=args.network,
        symbol=args.symbol,
        live=args.live,
        poll_seconds=args.poll_seconds,
        position_size_usdt=args.size_usdt,
        leverage=args.leverage,
        target_profit_pct=args.target_profit_pct,
        stop_loss_pct=args.stop_loss_pct,
        max_hold_minutes=args.max_hold_minutes,
        midpoint_ratio=args.midpoint_ratio,
        n_lock_n_threshold=args.n_lock_n_threshold,
        n_lock_n_buffer=args.n_lock_n_buffer,
    )

    # 安全保護：mainnet + live 時再提醒一次
    if cfg.live and cfg.network == "mainnet":
        print("🚨 警告：你正在使用 mainnet + --live，將進行真實交易。")

    # 0 代表無限跑（直到 Ctrl-C）
    cfg.runtime_minutes = None if args.runtime_minutes <= 0 else float(args.runtime_minutes)
    return cfg


def main() -> None:
    cfg = parse_args()

    try:
        asyncio.run(run(cfg, cfg.runtime_minutes))
    except KeyboardInterrupt:
        print(f"[{_utc_ts()}] 👋 Ctrl-C，結束")


if __name__ == "__main__":
    main()
