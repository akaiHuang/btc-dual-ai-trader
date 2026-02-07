"""
BacktesterRunner: step-by-step wrapper over MarketReplayEngine + HistoricalDataLoader.
 - Processes historical events (orderbook + trades) to update OBI/VPIN/Spread/Depth/Signed Volume.
 - Observation: vector from MarketReplayEngine.get_market_data()
 - Action: ActionSpec (mode determines LONG/SHORT/HOLD) -> open/close Position
 - Reward: Δequity (realized + unrealized - fees) per step

Non-intrusive: lives in ai_dev; does not modify runtime code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
import pandas as pd

from ai_dev.hybrid_env import ActionSpec


@dataclass
class BacktesterRunnerConfig:
    symbol: str = "BTCUSDT"
    capital: float = 100.0
    data_dir: Path = Path("data/historical")
    start_date: str = "2024-11-10"
    end_date: str = "2024-11-10"
    max_events: int | None = 5000  # limit for quick experiments
    default_stop: float = 5.0
    default_take: float = 8.0
    fee_rate: float = 0.0005
    step_events: int = 50  # number of market events to process per env step


class BacktesterRunner:
    def __init__(self, cfg: BacktesterRunnerConfig):
        self.cfg = cfg
        self.engine = None
        self.PositionCls = None
        self.events: List[dict] = []
        self.idx = 0
        self.position = None  # Position instance
        self.balance = cfg.capital
        self.equity = cfg.capital
        self.last_unrealized = 0.0
        self._init_engine_and_events()

    def _init_engine_and_events(self):
        try:
            from src.backtesting.market_replay_engine import MarketReplayEngine, Position  # type: ignore
            from src.backtesting.historical_data_loader import HistoricalDataLoader  # type: ignore
        except Exception as exc:
            raise NotImplementedError("Import MarketReplayEngine/HistoricalDataLoader failed.") from exc

        self.engine = MarketReplayEngine(symbol=self.cfg.symbol, capital=self.cfg.capital, data_dir=str(self.cfg.data_dir))
        self.PositionCls = Position
        loader = HistoricalDataLoader(data_dir=str(self.cfg.data_dir))
        loader.load_klines(self.cfg.symbol, "1m", self.cfg.start_date, self.cfg.end_date)
        events = loader.get_market_events(self.cfg.start_date, self.cfg.end_date)
        if self.cfg.max_events:
            events = events[: self.cfg.max_events]
        self.events = events
        self.idx = 0
        self.position = None
        self.balance = self.cfg.capital
        self.equity = self.cfg.capital
        self.last_unrealized = 0.0

    def _process_events(self, count: int):
        processed = 0
        latest_ts = None
        while self.idx < len(self.events) and processed < count:
            evt = self.events[self.idx]
            ts = evt["timestamp"]
            ts_ms = ts.timestamp() * 1000 if hasattr(ts, "timestamp") else None
            if evt["type"] == "ORDERBOOK":
                self.engine.process_orderbook(evt["data"])
            elif evt["type"] == "TRADE":
                self.engine.process_trade(evt["data"])
            latest_ts = ts_ms
            self.idx += 1
            processed += 1
        return latest_ts

    def _obs_vec(self):
        market_data = self.engine.get_market_data()
        if not market_data:
            return np.zeros(8, dtype=np.float32)
        return np.array(
            [
                market_data.get("price", 0.0),
                market_data.get("obi", 0.0),
                market_data.get("obi_velocity", 0.0),
                market_data.get("microprice", 0.0),
                market_data.get("microprice_pressure", 0.0),
                market_data.get("signed_volume", 0.0),
                market_data.get("vpin", 0.0),
                market_data.get("spread_bps", 0.0),
            ],
            dtype=np.float32,
        )

    def reset(self) -> dict:
        self._init_engine_and_events()
        obs = self._obs_vec()
        return {"features": obs, "position_state": {"has_position": False, "direction": "NEUTRAL", "unrealized_pnl_pct": 0.0}}

    def _apply_action(self, spec: ActionSpec, price: float, ts_ms: float):
        direction = "HOLD"
        if "SHORT" in spec.mode.upper():
            direction = "SHORT"
        elif "LONG" in spec.mode.upper():
            direction = "LONG"

        step_realized = 0.0
        # Exit logic (SL/TP/change direction)
        if self.position:
            exit_res = self.position.check_exit(price)
            if exit_res:
                reason, _ = exit_res
                self.position.close(price, reason, ts_ms)
                step_realized += self.position.pnl_usdt
                self.balance += self.position.pnl_usdt
                self.position = None
            elif self.position.direction != direction and direction != "HOLD":
                self.position.close(price, "SIGNAL_CHANGE", ts_ms)
                step_realized += self.position.pnl_usdt
                self.balance += self.position.pnl_usdt
                self.position = None

        # Entry
        if not self.position and direction != "HOLD":
            size = spec.position_size
            lev = spec.leverage or  self.cfg.capital  # keep as provided
            self.position = self.PositionCls(
                entry_price=price,
                direction=direction,
                size=size,
                leverage=lev,
                stop_loss_pct=spec.sl_pct or self.cfg.default_stop,
                take_profit_pct=spec.tp_pct or self.cfg.default_take,
                timestamp=ts_ms,
                capital=self.balance,
            )
            step_realized -= self.position.entry_fee

        return step_realized, direction

    def step(self, spec: ActionSpec) -> Tuple[dict, dict]:
        if self.idx >= len(self.events):
            obs = self._obs_vec()
            return {"features": obs, "position_state": {"has_position": False, "direction": "NEUTRAL"}}, {
                "pnl": 0.0,
                "fees": 0.0,
                "done": True,
            }

        ts_ms = self._process_events(self.cfg.step_events)
        price = self.engine.latest_price
        realized, direction = self._apply_action(spec, price, ts_ms or 0)

        unrealized = 0.0
        if self.position:
            unrealized, pnl_pct = self.position.get_unrealized_pnl(price)
        else:
            pnl_pct = 0.0

        prev_equity = self.equity
        self.equity = self.balance + unrealized
        reward = realized + (self.equity - prev_equity)
        obs = self._obs_vec()
        done = self.idx >= len(self.events)
        pos_state = {
            "has_position": self.position is not None,
            "direction": self.position.direction if self.position else "NEUTRAL",
            "unrealized_pnl_pct": pnl_pct,
        }
        info = {"pnl": float(reward), "fees": 0.0, "done": done, "price": price, "equity": self.equity}
        return {"features": obs, "position_state": pos_state}, info
