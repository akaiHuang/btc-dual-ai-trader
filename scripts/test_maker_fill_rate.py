#!/usr/bin/env python3
"""
🧪 Maker 成交率測試 (精簡版)
測試 Maker 訂單成交機率，支援 M%M 階梯式鎖利
使用: python scripts/test_maker_fill_rate.py --mainnet --hours 8 --size 20 --leverage 50
"""

import asyncio, argparse, time, json, math, random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

# ANSI 顏色
class C:
    R, G, Y, B, M, CN, DIM, BD, RST = '\033[91m', '\033[92m', '\033[93m', '\033[94m', '\033[95m', '\033[96m', '\033[2m', '\033[1m', '\033[0m'
    @staticmethod
    def c(t, color): return f"{color}{t}{C.RST}"
    @staticmethod
    def red(t): return C.c(t, C.R)
    @staticmethod
    def green(t): return C.c(t, C.G)
    @staticmethod
    def yellow(t): return C.c(t, C.Y)
    @staticmethod
    def cyan(t): return C.c(t, C.CN)
    @staticmethod
    def magenta(t): return C.c(t, C.M)
    @staticmethod
    def dim(t): return C.c(t, C.DIM)
    @staticmethod
    def bold(t): return C.c(t, C.BD)
    @staticmethod
    def pnl(v, fmt="${:.4f}"): return C.green(fmt.format(v)) if v > 0 else C.red(fmt.format(v)) if v < 0 else fmt.format(v)
    @staticmethod
    def rate(v): return C.green(f"{v:.1f}%") if v >= 60 else C.yellow(f"{v:.1f}%") if v >= 40 else C.red(f"{v:.1f}%")

try:
    from scripts.dydx_whale_trader import DydxAPI, DydxConfig
    from src.dydx_data_hub import DydxDataHub
except ImportError as e:
    print(f"❌ 導入失敗: {e}")
    sys.exit(1)


class MakerFillRateTest:
    def __init__(self, testnet=True, size_usdc=20.0, maker_timeout=2.0, drift_windows=None,
                 price_sources=None, entry_anchors=None, tp_bps=5.0, sl_bps=0.5, max_hold=30.0,
                 maker_exit_timeout=2.0, poll_interval=0.25, leverage=50, midpoint_ratio=0.8,
                 no_timeout=False, max_loss=0.0, max_spread=0.0):
        self.testnet, self.size_usdc, self.maker_timeout = testnet, size_usdc, maker_timeout
        self.no_timeout, self.max_hold, self.poll_interval = no_timeout, max_hold, poll_interval
        self.tp_bps, self.sl_bps, self.leverage = tp_bps, sl_bps, leverage
        self.midpoint_ratio, self.maker_exit_timeout = midpoint_ratio, maker_exit_timeout
        self.max_loss, self.max_spread = max_loss, max_spread
        self.drift_windows = sorted(drift_windows or [0.5, 1.0, 2.0, 5.0])
        self.price_sources = [s for s in (price_sources or ["api"]) if s in ("api", "ws")] or ["api"]
        self.entry_anchors = [a for a in (entry_anchors or ["mid"]) if a in ("bid", "ask", "mid", "mid+")] or ["mid"]
        self.api, self.ws_hub = None, None
        self.results, self.maker_fills, self.maker_misses = [], 0, 0
        self.total_trades, self.wins, self.losses, self.total_pnl = 0, 0, 0, 0.0
        self.best_pnl, self.worst_pnl = float('-inf'), float('inf')
        # API 速率限制 (10次/秒)
        self._api_calls = []  # 記錄調用時間戳
        self._api_limit = 10  # 每秒最大調用次數
        # 隨機波次
        self.random_mode, self.current_wave = True, 1
        self.wave1, self.wave2 = self._gen_wave(11), self._gen_wave(20)
        self.w1_idx, self.w2_idx = 0, 0

    def _gen_wave(self, n): 
        w = ["LONG"]*(n//2) + ["SHORT"]*(n-n//2)
        random.shuffle(w)
        return w

    def _next_dir(self):
        if self.current_wave == 1:
            if self.w1_idx < len(self.wave1): return self.wave1[self.w1_idx]
            self.current_wave, self.wave1, self.w1_idx = 2, self._gen_wave(20), 0
        if self.w2_idx < len(self.wave2): return self.wave2[self.w2_idx]
        self.current_wave, self.wave2, self.w2_idx = 1, self._gen_wave(20), 0
        return self._next_dir()

    def _consume_dir(self):
        if self.current_wave == 1: self.w1_idx += 1
        else: self.w2_idx += 1

    def _print_waves(self):
        def fmt(w, idx): return ", ".join(C.dim("⚫") if i < idx else (C.green("🟢") if d=="LONG" else C.red("🔴")) for i, d in enumerate(w))
        m1, m2 = (C.yellow(" ◀"), "") if self.current_wave == 1 else ("", C.yellow(" ◀"))
        print(f"\n{C.cyan('🎲 隨機模式')}  W1: {fmt(self.wave1, self.w1_idx)}{m1}  W2: {fmt(self.wave2, self.w2_idx)}{m2}")

    @staticmethod
    def _bps(a, b): return (a - b) / b * 10000 if b > 0 else 0.0

    def _calc_limit_price(self, dir, bid, ask, anchor):
        spread, mid = ask - bid, (bid + ask) / 2
        safety = max(spread * 0.2, 1.0)
        if dir == "LONG":
            return {"ask": max(bid, ask-safety), "mid": mid, "mid+": mid+spread*0.25}.get(anchor, bid), safety
        return {"bid": min(ask, bid+safety), "mid": mid, "mid+": mid-spread*0.25}.get(anchor, ask), safety

    def _calc_tp_sl(self, dir, entry):
        tp_f, sl_f = (self.tp_bps/100)/self.leverage, (self.sl_bps/100)/self.leverage
        return (entry*(1+tp_f), entry*(1-sl_f)) if dir == "LONG" else (entry*(1-tp_f), entry*(1+sl_f))

    def _calc_pnl_pct(self, dir, entry, cur):
        if entry <= 0: return 0.0
        return ((cur - entry) / entry * 100) if dir == "LONG" else ((entry - cur) / entry * 100)

    def _calc_mpm_sl(self, peak_pct):
        """M%M 階梯鎖利: ROE<0.7%→None, 0.7-0.99%→+0.4%, >=1%→階梯(鎖住階段下限)"""
        roe = peak_pct * self.leverage
        if roe < 0.5: return None, "未達門檻"
        if roe < 1.0: return 0.4 / self.leverage, "保本"
        # 從 1% 開始，每 0.5% 為一個階段，鎖利 = 階段下限
        # 1.0-1.49→+1%, 1.5-1.99→+1.5%, 2.0-2.49→+2%, ...
        sl_roe = math.floor(roe * 2) / 2  # 取 0.5 倍數下限
        stage = int((sl_roe - 0.5) * 2)   # 階段編號
        return sl_roe / self.leverage, f"階段{stage}"

    def _print_stats(self, prefix="📊"):
        if self.total_trades == 0: return
        wr = self.wins / self.total_trades * 100
        draws = self.total_trades - self.wins - self.losses
        best = C.green(f"${self.best_pnl:+.4f}") if self.best_pnl != float('-inf') else "-"
        worst = C.red(f"${self.worst_pnl:+.4f}") if self.worst_pnl != float('inf') else "-"
        # 計算收益率
        roi = (self.total_pnl / self.size_usdc) * 100 if self.size_usdc > 0 else 0
        print(f"\n   {C.cyan(f'{prefix} 統計:')} {self.total_trades}筆 | 勝{C.green(self.wins)} 敗{C.red(self.losses)} 平{draws} | 勝率{C.rate(wr)}")
        print(f"   💰 本金: ${self.size_usdc:.2f} | 盈虧: {C.pnl(self.total_pnl)} ({C.pnl(roi, '{:+.2f}%')}) | 最佳{best} 最差{worst}")

    async def _cleanup_pos(self, ctx=""):
        try:
            for pos in (await self.api.get_positions() or []):
                if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                    sz = abs(float(pos.get('size', 0)))
                    if sz > 0.00001:
                        print(f"   {C.yellow(f'🧹 清理{ctx}:')} {pos.get('side')} {sz:.4f} BTC")
                        await self.api._close_ioc_order(pos.get('side'), sz)
                        await asyncio.sleep(0.5)
                        return True
        except: pass
        return False

    async def connect(self):
        cfg = DydxConfig()
        cfg.network = "testnet" if self.testnet else "mainnet"
        cfg.paper_trading, cfg.sync_real_trading = False, True
        cfg.maker_fee_pct = cfg.taker_fee_pct = 0.0
        self.api = DydxAPI(cfg)
        await self.api.connect()
        if not self.api.node:
            await self.api._init_node_client()
        if not self.api.node:
            raise Exception("❌ Node 連接失敗")
        self.ws_hub = DydxDataHub(symbol="BTC-USD", network=cfg.network)
        self.ws_hub.start()
        for _ in range(50):
            d = self.ws_hub.get_data()
            if d.current_price > 0 or (d.bid_price > 0 and d.ask_price > 0): break
            await asyncio.sleep(0.2)
        print(f"✅ 連接 dYdX {'Testnet' if self.testnet else 'Mainnet'}")

    async def _rate_limit(self):
        """確保 API 調用不超過 10次/秒"""
        now = time.time()
        self._api_calls = [t for t in self._api_calls if now - t < 1.0]  # 保留 1 秒內的
        if len(self._api_calls) >= self._api_limit:
            wait = 1.0 - (now - self._api_calls[0]) + 0.01
            if wait > 0:
                await asyncio.sleep(wait)
        self._api_calls.append(time.time())

    async def get_book(self):
        await self._rate_limit()
        bid, ask = await self.api.get_best_bid_ask()
        return bid, ask, (bid+ask)/2

    def get_ws(self):
        if not self.ws_hub: return {"bid": 0, "ask": 0, "mid": 0, "age_ms": 0}
        d = self.ws_hub.get_data()
        bid, ask = float(d.bid_price or 0), float(d.ask_price or 0)
        mid = float(d.current_price or 0) or ((bid+ask)/2 if bid and ask else 0)
        age = (time.time() - float(d.last_update or 0)) * 1000 if d.last_update else 0
        return {"bid": bid, "ask": ask, "mid": mid, "age_ms": age}

    async def get_snap(self):
        """統一使用 API 獲取即時價格"""
        bid, ask, mid = await self.get_book()
        return bid, ask, mid, {"bid": bid, "ask": ask, "mid": mid, "age_ms": 0, "src": "api"}

    async def _wait_exit(self, dir, entry, btc_sz):
        tp, sl = self._calc_tp_sl(dir, entry)
        start, peak, last_print = time.time(), 0.0, 0.0
        while self.no_timeout or (time.time() - start < self.max_hold):
            bid, ask, mid = await self.get_book()  # 直接用 API
            pnl_pct = self._calc_pnl_pct(dir, entry, mid)
            peak = max(peak, pnl_pct)
            sl_pct, stage = self._calc_mpm_sl(peak)
            cur_sl = entry * (1 + sl_pct/100) if sl_pct and dir == "LONG" else entry * (1 - sl_pct/100) if sl_pct else sl
            # 打印狀態
            if time.time() - last_print >= 2.0:
                roe = pnl_pct * self.leverage
                pnl_u = (mid - entry) * btc_sz if dir == "LONG" else (entry - mid) * btc_sz
                sl_info = f"🔒{stage} ROE{C.pnl(sl_pct*self.leverage if sl_pct else -self.sl_bps, '{:+.1f}%')}" if sl_pct else f"SL ROE{C.red(f'-{self.sl_bps:.1f}%')}"
                dir_c = C.green(f"🟢{dir}") if dir == "LONG" else C.red(f"🔴{dir}")
                print(f"\n   {dir_c} ${entry:,.2f} | ROE{C.pnl(roe, '{:+.2f}%')} ({C.pnl(pnl_u)}) | Peak{peak*self.leverage:.1f}% | {sl_info}")
                last_print = time.time()
            # 檢查 TP/SL
            if (dir == "LONG" and mid >= tp) or (dir == "SHORT" and mid <= tp):
                return "TP", bid, ask, mid, {"src": "api"}, time.time()-start, peak
            is_sl = (dir == "LONG" and mid <= cur_sl) or (dir == "SHORT" and mid >= cur_sl)
            if is_sl:
                sig = "M%M_LOCK" if sl_pct and sl_pct >= 0 else "SL"
                print(f"\n   {C.yellow('⚡') if sig=='M%M_LOCK' else C.red('⚡')} 觸發{sig}! mid=${mid:.2f} {'<=' if dir=='LONG' else '>='} ${cur_sl:.2f}")
                return sig, bid, ask, mid, {"src": "api"}, time.time()-start, peak
            await asyncio.sleep(self.poll_interval)
        bid, ask, mid = await self.get_book()
        return "TIMEOUT", bid, ask, mid, {"src": "api"}, time.time()-start, peak

    async def run_single(self, num, dir, src, anchor):
        dir_s = C.green(f"🟢{dir}") if dir == "LONG" else C.red(f"🔴{dir}")
        print(f"\n{C.cyan('='*50)}\n{C.bold(f'#{num}')} {dir_s} ({C.yellow(anchor)})\n{C.cyan('='*50)}")
        result = {"test_num": num, "direction": dir, "timestamp": datetime.now().isoformat(),
                  "maker_filled": False, "pnl_usdt": 0.0, "net_pnl_usdt": 0.0, "error": None}
        try:
            bid, ask, mid, _ = await self.get_snap()
            spread = self._bps(ask, bid)
            result.update({"bid_at_place": bid, "ask_at_place": ask, "mid_at_place": mid})
            # 直接使用 API 價格（更即時）
            o_bid, o_ask = bid, ask
            o_mid = mid
            o_spread = spread
            print(f"📈 Bid={bid:.2f} Mid={mid:.2f} Ask={ask:.2f} | Spread={spread:.1f}bps (API)")
            if self.max_spread > 0 and o_spread > self.max_spread:
                print(f"   ⏸️ Spread太大，跳過")
                result["error"] = "spread_too_high"
                return result
            # 計算 Maker 價格
            limit, _ = self._calc_limit_price(dir, o_bid, o_ask, anchor)
            btc_sz = round(max((self.size_usdc * self.leverage) / mid, 0.0001), 4)
            print(f"📦 {btc_sz:.4f} BTC (${self.size_usdc}×{self.leverage}X) | Maker價: {limit:.2f}")
            # 下單
            tx, fill = await self.api._try_place_order(side=dir, size=btc_sz, timeout_seconds=self.maker_timeout,
                                                        attempt=1, max_attempts=1, best_bid=o_bid, best_ask=o_ask, limit_price=limit)
            # 檢查延遲成交 (用 limit 作為成交價，因為 POST_ONLY 不會有滑點)
            if not tx or fill <= 0:
                await asyncio.sleep(0.5)
                for pos in (await self.api.get_positions() or []):
                    if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN' and abs(float(pos.get('size', 0))) >= 0.0001:
                        fill = limit  # ✅ 用下單價，不用持倉均價
                        tx = "delayed"
                        break
            if tx and fill > 0:
                # ✅ 記錄實際下單價 (POST_ONLY = 零滑點)
                actual_entry = fill if fill > 0 else limit
                result["maker_filled"], result["maker_price"] = True, actual_entry
                self.maker_fills += 1
                if self.random_mode: self._consume_dir()
                # 驗證持倉大小 (但不覆蓋入場價)
                await asyncio.sleep(0.2)
                for pos in (await self.api.get_positions() or []):
                    if pos.get('market') == 'BTC-USD' and pos.get('status') == 'OPEN':
                        btc_sz = abs(float(pos.get('size', btc_sz)))  # ✅ 只取大小，不取均價
                        break
                fill = actual_entry  # ✅ 確保用正確的入場價
                slip = self._bps(fill, limit) if dir == "LONG" else self._bps(limit, fill)
                result["entry_slippage_bps"] = slip
                print(f"{C.green('✅ 成交!')} ${fill:.2f} | 滑點{C.pnl(-slip, '{:+.1f}bps')}")
                # 等待出場
                sig, b2, a2, m2, ws2, hold, peak = await self._wait_exit(dir, fill, btc_sz)
                result.update({"exit_reason": sig, "hold_seconds": hold, "peak_pnl_pct": peak})
                # 平倉
                exit_side = "SHORT" if dir == "LONG" else "LONG"
                if sig == "TP":
                    print(f"\n🎯 {sig}，嘗試Maker平倉...")
                    ex_tx, ex_p = await self.api._try_place_order(side=exit_side, size=btc_sz, timeout_seconds=self.maker_exit_timeout,
                                                                   attempt=1, max_attempts=1, best_bid=b2, best_ask=a2)
                    if not ex_tx or ex_p <= 0:
                        print(f"   {C.yellow('⚠️ Maker超時→Taker')}")
                        ex_tx, ex_p = await self.api._close_ioc_order(dir, btc_sz)
                    result["exit_method"] = f"{sig}_{'MAKER' if ex_p > 0 else 'TAKER'}"
                elif sig == "M%M_LOCK":
                    print(f"\n🔐 {sig}，Taker立即平倉(避免滑點)...")
                    ex_tx, ex_p = await self.api._close_ioc_order(dir, btc_sz)
                    result["exit_method"] = f"{sig}_TAKER"
                else:
                    print(f"\n{'🛑' if sig=='SL' else '⏲️'} {sig}，Taker平倉...")
                    ex_tx, ex_p = await self.api._close_ioc_order(dir, btc_sz)
                    result["exit_method"] = f"{sig}_TAKER"
                if ex_tx and ex_p > 0:
                    result["exit_price"] = ex_p
                    pnl = (ex_p - fill) * btc_sz if dir == "LONG" else (fill - ex_p) * btc_sz
                    result["pnl_usdt"] = result["net_pnl_usdt"] = pnl
                    roe = self._calc_pnl_pct(dir, fill, ex_p) * self.leverage
                    print(f"{C.green('✅ 平倉')} ${ex_p:.2f} | PnL{C.pnl(pnl)} | ROE{C.pnl(roe, '{:+.2f}%')}")
                    self.total_trades += 1
                    self.total_pnl += pnl
                    if pnl > 0: self.wins += 1
                    elif pnl < 0: self.losses += 1
                    self.best_pnl, self.worst_pnl = max(self.best_pnl, pnl), min(self.worst_pnl, pnl)
                    self._print_stats()
                    await asyncio.sleep(0.3)
                    await self._cleanup_pos("殘留")
                else:
                    result["error"] = "exit_failed"
                    print(C.red("❌ 平倉失敗"))
                    await self._cleanup_pos("平倉失敗")
            else:
                self.maker_misses += 1
                print(C.red("❌ Maker未成交"))
                await self._cleanup_pos("未成交")
        except Exception as e:
            result["error"] = str(e)
            print(f"❌ 錯誤: {e}")
            await self._cleanup_pos("異常")
        self.results.append(result)
        return result

    async def run_all(self, num_trades=0, hours=0):
        mode = "hours" if hours > 0 else "trades"
        end_time = time.time() + hours * 3600 if hours > 0 else None
        num_trades = num_trades or 10
        net = C.green('Mainnet') if not self.testnet else C.yellow('Testnet')
        print(f"\n{C.cyan('='*60)}\n{C.bold('🚀 Maker成交率測試')} | {net} | ${self.size_usdc} | {self.leverage}X")
        print(f"   TP:{C.green(f'+{self.tp_bps}%')} SL:{C.red(f'-{self.sl_bps}%')} | M%M:{self.midpoint_ratio*100:.0f}%")
        if self.max_loss > 0: print(f"   {C.red(f'🛑 最大虧損: ${self.max_loss}')}")
        print(f"{C.cyan('='*60)}")
        await self.connect()
        if self.random_mode: self._print_waves()
        interrupted = False
        try:
            cnt = 0
            while True:
                if mode == "hours" and time.time() >= end_time:
                    print(f"\n{C.green('⏱️ 時間到')}")
                    break
                if mode == "trades" and cnt >= num_trades: break
                cnt += 1
                dir = self._next_dir() if self.random_mode else ["LONG", "SHORT"][(cnt-1) % 2]
                anchor = self.entry_anchors[(cnt-1) % len(self.entry_anchors)]
                src = self.price_sources[(cnt-1) % len(self.price_sources)]
                if self.random_mode: self._print_waves()
                await self.run_single(cnt, dir, src, anchor)
                if self.max_loss > 0 and self.total_pnl < -self.max_loss:
                    print(f"\n{C.red('🛑 達到最大虧損!')}")
                    break
                print(f"\n{C.dim('⏳ 3秒...')}")
                await asyncio.sleep(3)
        except (KeyboardInterrupt, asyncio.CancelledError):
            interrupted = True
            print(f"\n{C.yellow('='*60)}")
            print(f"{C.yellow('⚠️ 使用者中斷 (Ctrl+C)')}")
            print(f"{C.yellow('='*60)}")
        except Exception as e:
            print(f"\n{C.red(f'❌ 錯誤: {e}')}")
            interrupted = True
        finally:
            try:
                if self.ws_hub: self.ws_hub.stop()
            except: pass
            # 無論正常結束或中斷，都輸出統計並保存
            if self.results:
                print(f"\n{C.cyan('正在保存記錄...')}")
                self._print_summary()
                self._save()
            else:
                print(f"\n{C.yellow('沒有交易記錄')}")
            if interrupted:
                try:
                    await self._cleanup_pos("中斷")
                except: pass

    def _print_summary(self):
        print(f"\n{C.cyan('='*60)}\n{C.bold('📊 測試摘要')}\n{C.cyan('='*60)}")
        total = len(self.results)
        filled = sum(1 for r in self.results if r["maker_filled"])
        fr = filled / total * 100 if total else 0
        print(f"   Maker成交: {C.green(filled)}/{total} ({C.rate(fr)})")
        filled_r = [r for r in self.results if r["maker_filled"]]
        if filled_r:
            pnl = sum(r["pnl_usdt"] for r in filled_r)
            wins = sum(1 for r in filled_r if r["pnl_usdt"] > 0)
            wr = wins / len(filled_r) * 100
            balance = self.size_usdc + pnl
            roi = (pnl / self.size_usdc) * 100 if self.size_usdc > 0 else 0
            print(f"   💰 本金: ${self.size_usdc:.2f} → 餘額: {C.pnl(balance, '${:.4f}')} | 盈虧: {C.pnl(pnl)} ({C.pnl(roi, '{:+.2f}%')})")
            print(f"   勝率: {C.rate(wr)} ({wins}/{len(filled_r)})")
            # 按出場方式
            by_exit = {}
            for r in filled_r:
                k = r.get("exit_method", "-")
                by_exit.setdefault(k, []).append(r)
            print(f"\n   📋 出場方式:")
            for k, rs in sorted(by_exit.items()):
                p = sum(r["pnl_usdt"] for r in rs)
                print(f"      {k}: {len(rs)}筆 | PnL{C.pnl(p)}")
        # 最終
        print(f"\n{C.cyan('='*60)}\n{C.bold('🏁 最終')}")
        self._print_stats("🏁")
        print(f"{C.cyan('='*60)}")

    def _save(self):
        p = Path("logs/maker_test")
        p.mkdir(parents=True, exist_ok=True)
        f = p / f"maker_{datetime.now():%Y%m%d_%H%M%S}.json"
        
        # 從 results 重新計算統計（確保一致性）
        filled_r = [r for r in self.results if r.get("maker_filled")]
        calc_pnl = sum(r.get("pnl_usdt", 0) for r in filled_r)
        calc_wins = sum(1 for r in filled_r if r.get("pnl_usdt", 0) > 0)
        calc_losses = sum(1 for r in filled_r if r.get("pnl_usdt", 0) < 0)
        calc_draws = len(filled_r) - calc_wins - calc_losses
        
        with open(f, 'w') as fp:
            json.dump({
                "config": {
                    "testnet": self.testnet, "size": self.size_usdc, "leverage": self.leverage,
                    "tp_bps": self.tp_bps, "sl_bps": self.sl_bps, "timestamp": datetime.now().isoformat()
                },
                "summary": {
                    "initial_capital": self.size_usdc,
                    "final_balance": self.size_usdc + calc_pnl,
                    "total_pnl": calc_pnl,
                    "roi_pct": (calc_pnl / self.size_usdc) * 100 if self.size_usdc > 0 else 0,
                    "total_attempts": len(self.results),
                    "maker_fills": len(filled_r),
                    "fill_rate_pct": len(filled_r) / len(self.results) * 100 if self.results else 0,
                    "wins": calc_wins, "losses": calc_losses, "draws": calc_draws,
                    "win_rate_pct": calc_wins / len(filled_r) * 100 if filled_r else 0,
                    "avg_pnl": calc_pnl / len(filled_r) if filled_r else 0,
                    "best_pnl": max((r.get("pnl_usdt", 0) for r in filled_r), default=0),
                    "worst_pnl": min((r.get("pnl_usdt", 0) for r in filled_r), default=0),
                },
                "trades": self.results
            }, fp, indent=2, ensure_ascii=False)
        print(f"\n💾 已保存: {f}")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--testnet", action="store_true", default=True)
    p.add_argument("--mainnet", action="store_true")
    p.add_argument("--trades", type=int, default=0)
    p.add_argument("--hours", type=float, default=0)
    p.add_argument("--size", type=float, default=20.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--tp-bps", type=float, default=5.0)
    p.add_argument("--sl-bps", type=float, default=0.5)
    p.add_argument("--max-hold", type=float, default=30.0)
    p.add_argument("--no-timeout", action="store_true")
    p.add_argument("--poll-interval", type=float, default=0.1)
    p.add_argument("--leverage", type=int, default=50)
    p.add_argument("--midpoint-ratio", type=float, default=0.8)
    p.add_argument("--no-random", action="store_true")
    p.add_argument("--max-loss", type=float, default=0.0)
    p.add_argument("--max-spread", type=float, default=0.0)
    p.add_argument("--maker-exit-timeout", type=float, default=2.0)
    p.add_argument("--price-sources", type=str, default="ws")
    p.add_argument("--entry-anchors", type=str, default="mid")
    p.add_argument("--drift-windows", type=str, default="")
    a = p.parse_args()
    
    t = MakerFillRateTest(
        testnet=not a.mainnet, size_usdc=a.size, maker_timeout=a.timeout,
        drift_windows=[float(x) for x in a.drift_windows.split(",") if x.strip()] or None,
        price_sources=[x.strip() for x in a.price_sources.split(",") if x.strip()],
        entry_anchors=[x.strip() for x in a.entry_anchors.split(",") if x.strip()],
        tp_bps=a.tp_bps, sl_bps=a.sl_bps, max_hold=a.max_hold,
        maker_exit_timeout=a.maker_exit_timeout, poll_interval=a.poll_interval,
        leverage=a.leverage, midpoint_ratio=a.midpoint_ratio,
        no_timeout=a.no_timeout, max_loss=a.max_loss, max_spread=a.max_spread
    )
    t.random_mode = not a.no_random
    await t.run_all(a.trades, a.hours)


if __name__ == "__main__":
    import signal
    
    def handle_signal(signum, frame):
        print(f"\n{C.yellow('⚠️ 收到信號 (Ctrl+C)')}")
        raise KeyboardInterrupt()
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print(f"\n{C.yellow('👋 退出')}")
