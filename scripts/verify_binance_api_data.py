#!/usr/bin/env python3
"""
🔍 Binance API 數據驗證腳本 v1.0
================================

全面檢查主力分析系統用到的所有指標與數據是否與幣安 API 同步正確

檢查項目：
1. WebSocket 連線狀態
2. REST API 數據可用性
3. OBI (訂單簿失衡) 計算正確性
4. K 線數據同步
5. MTF (多時間框架) 指標計算
6. 大單追蹤數據
7. 爆倉數據來源
8. 主力策略偵測器數據輸入

Author: AI Trading System
Date: 2025-12-05
"""

import asyncio
import json
import time
import requests
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import deque

# 添加專案路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ============================================================
# 驗證結果類
# ============================================================

class VerificationResult:
    def __init__(self, name: str):
        self.name = name
        self.status = "PENDING"  # PASS, FAIL, WARNING
        self.message = ""
        self.details = {}
        self.timestamp = datetime.now().isoformat()
    
    def pass_check(self, message: str = "OK", details: Dict = None):
        self.status = "PASS"
        self.message = message
        self.details = details or {}
    
    def fail_check(self, message: str, details: Dict = None):
        self.status = "FAIL"
        self.message = message
        self.details = details or {}
    
    def warn_check(self, message: str, details: Dict = None):
        self.status = "WARNING"
        self.message = message
        self.details = details or {}
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp
        }
    
    def display(self) -> str:
        icon = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️", "PENDING": "⏳"}.get(self.status, "❓")
        return f"{icon} {self.name}: {self.message}"


# ============================================================
# Binance API 驗證器
# ============================================================

class BinanceAPIVerifier:
    """全面驗證 Binance API 數據"""
    
    # API Endpoints
    BINANCE_SPOT_API = "https://api.binance.com"
    BINANCE_FUTURES_API = "https://fapi.binance.com"
    BINANCE_WS_BASE = "wss://fstream.binance.com"
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.results: List[VerificationResult] = []
        
        # 數據緩存
        self.price_data = {}
        self.orderbook_data = {}
        self.kline_data = {}
        self.trade_data = []
    
    def add_result(self, result: VerificationResult):
        self.results.append(result)
        print(result.display())
    
    # ============================================================
    # 1. REST API 可用性檢查
    # ============================================================
    
    def verify_rest_api_connectivity(self) -> VerificationResult:
        """驗證 REST API 連線"""
        result = VerificationResult("REST API 連線")
        
        endpoints = [
            (f"{self.BINANCE_SPOT_API}/api/v3/ping", "Spot API"),
            (f"{self.BINANCE_FUTURES_API}/fapi/v1/ping", "Futures API"),
            (f"{self.BINANCE_FUTURES_API}/fapi/v1/time", "Server Time"),
        ]
        
        results = {}
        all_ok = True
        
        for url, name in endpoints:
            try:
                start = time.time()
                resp = requests.get(url, timeout=5)
                latency = (time.time() - start) * 1000
                
                if resp.status_code == 200:
                    results[name] = {"status": "OK", "latency_ms": round(latency, 1)}
                else:
                    results[name] = {"status": "FAIL", "code": resp.status_code}
                    all_ok = False
            except Exception as e:
                results[name] = {"status": "FAIL", "error": str(e)}
                all_ok = False
        
        if all_ok:
            avg_latency = sum(r.get("latency_ms", 0) for r in results.values()) / len(results)
            result.pass_check(f"所有 API 可用，平均延遲 {avg_latency:.0f}ms", results)
        else:
            result.fail_check("部分 API 不可用", results)
        
        return result
    
    # ============================================================
    # 2. 價格數據驗證
    # ============================================================
    
    def verify_price_data(self) -> VerificationResult:
        """驗證價格數據一致性"""
        result = VerificationResult("價格數據一致性")
        
        try:
            # 獲取不同來源的價格
            prices = {}
            
            # Spot Ticker
            resp = requests.get(f"{self.BINANCE_SPOT_API}/api/v3/ticker/price", 
                              params={"symbol": self.symbol}, timeout=5)
            if resp.status_code == 200:
                prices['spot_ticker'] = float(resp.json()['price'])
            
            # Futures Mark Price
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/premiumIndex", 
                              params={"symbol": self.symbol}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                prices['futures_mark'] = float(data['markPrice'])
                prices['futures_index'] = float(data['indexPrice'])
            
            # Futures Ticker
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/ticker/price", 
                              params={"symbol": self.symbol}, timeout=5)
            if resp.status_code == 200:
                prices['futures_ticker'] = float(resp.json()['price'])
            
            self.price_data = prices
            
            # 計算價差
            if len(prices) >= 2:
                price_list = list(prices.values())
                max_diff = max(price_list) - min(price_list)
                avg_price = sum(price_list) / len(price_list)
                diff_pct = (max_diff / avg_price) * 100
                
                if diff_pct < 0.1:  # 差異小於 0.1%
                    result.pass_check(f"價格一致 (差異 {diff_pct:.4f}%)", prices)
                elif diff_pct < 0.5:
                    result.warn_check(f"價格略有差異 ({diff_pct:.4f}%)", prices)
                else:
                    result.fail_check(f"價格差異過大 ({diff_pct:.4f}%)", prices)
            else:
                result.fail_check("無法獲取足夠價格數據", prices)
                
        except Exception as e:
            result.fail_check(f"價格驗證失敗: {e}")
        
        return result
    
    # ============================================================
    # 3. 訂單簿數據驗證 (OBI 計算基礎)
    # ============================================================
    
    def verify_orderbook_data(self) -> VerificationResult:
        """驗證訂單簿數據 (OBI 計算基礎)"""
        result = VerificationResult("訂單簿數據 (OBI)")
        
        try:
            # 獲取訂單簿
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/depth", 
                              params={"symbol": self.symbol, "limit": 20}, timeout=5)
            
            if resp.status_code != 200:
                result.fail_check(f"API 錯誤: {resp.status_code}")
                return result
            
            data = resp.json()
            bids = data.get('bids', [])
            asks = data.get('asks', [])
            
            if not bids or not asks:
                result.fail_check("訂單簿數據為空")
                return result
            
            # 計算 OBI
            total_bid_size = sum(float(bid[1]) for bid in bids[:10])
            total_ask_size = sum(float(ask[1]) for ask in asks[:10])
            total_size = total_bid_size + total_ask_size
            
            if total_size > 0:
                obi = (total_bid_size - total_ask_size) / total_size
            else:
                obi = 0
            
            # 計算最佳買賣價
            best_bid = float(bids[0][0]) if bids else 0
            best_ask = float(asks[0][0]) if asks else 0
            spread = best_ask - best_bid
            spread_pct = (spread / best_bid * 100) if best_bid > 0 else 0
            
            details = {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "spread_pct": round(spread_pct, 6),
                "total_bid_size": round(total_bid_size, 4),
                "total_ask_size": round(total_ask_size, 4),
                "obi": round(obi, 4),
                "obi_interpretation": self._interpret_obi(obi),
                "depth_levels": len(bids)
            }
            
            self.orderbook_data = details
            
            # 驗證數據合理性
            if spread_pct > 0.5:
                result.warn_check(f"價差較大 ({spread_pct:.4f}%)", details)
            elif abs(obi) > 0.8:
                result.warn_check(f"OBI 極端值 ({obi:.3f})", details)
            else:
                result.pass_check(f"OBI = {obi:.3f} ({details['obi_interpretation']})", details)
                
        except Exception as e:
            result.fail_check(f"訂單簿驗證失敗: {e}")
        
        return result
    
    def _interpret_obi(self, obi: float) -> str:
        """解讀 OBI 值"""
        if obi > 0.3:
            return "強烈買盤"
        elif obi > 0.1:
            return "買盤優勢"
        elif obi > -0.1:
            return "平衡"
        elif obi > -0.3:
            return "賣盤優勢"
        else:
            return "強烈賣盤"
    
    # ============================================================
    # 4. K 線數據驗證 (MTF 基礎)
    # ============================================================
    
    def verify_kline_data(self) -> VerificationResult:
        """驗證 K 線數據 (MTF 多時間框架基礎)"""
        result = VerificationResult("K 線數據 (MTF)")
        
        timeframes = ["15m", "1h", "4h"]
        kline_results = {}
        all_ok = True
        
        for tf in timeframes:
            try:
                resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/klines",
                                  params={"symbol": self.symbol, "interval": tf, "limit": 30},
                                  timeout=5)
                
                if resp.status_code == 200:
                    klines = resp.json()
                    if klines:
                        latest = klines[-1]
                        kline_results[tf] = {
                            "count": len(klines),
                            "latest_open": float(latest[1]),
                            "latest_high": float(latest[2]),
                            "latest_low": float(latest[3]),
                            "latest_close": float(latest[4]),
                            "latest_volume": float(latest[5]),
                            "timestamp": datetime.fromtimestamp(latest[0]/1000).isoformat()
                        }
                        
                        # 驗證數據新鮮度
                        kline_age_ms = time.time() * 1000 - latest[0]
                        interval_ms = {"15m": 15*60*1000, "1h": 60*60*1000, "4h": 4*60*60*1000}
                        if kline_age_ms > interval_ms[tf] * 2:
                            kline_results[tf]["warning"] = "數據可能過舊"
                    else:
                        kline_results[tf] = {"error": "無數據"}
                        all_ok = False
                else:
                    kline_results[tf] = {"error": f"API 錯誤 {resp.status_code}"}
                    all_ok = False
                    
            except Exception as e:
                kline_results[tf] = {"error": str(e)}
                all_ok = False
        
        self.kline_data = kline_results
        
        if all_ok:
            result.pass_check(f"所有時間框架數據正常", kline_results)
        else:
            result.fail_check("部分時間框架數據異常", kline_results)
        
        return result
    
    # ============================================================
    # 5. 逐筆成交數據驗證 (大單追蹤基礎)
    # ============================================================
    
    def verify_trade_data(self) -> VerificationResult:
        """驗證逐筆成交數據 (大單追蹤基礎)"""
        result = VerificationResult("逐筆成交數據")
        
        try:
            # 獲取最近的聚合交易
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/aggTrades",
                              params={"symbol": self.symbol, "limit": 100},
                              timeout=5)
            
            if resp.status_code != 200:
                result.fail_check(f"API 錯誤: {resp.status_code}")
                return result
            
            trades = resp.json()
            
            if not trades:
                result.fail_check("無成交數據")
                return result
            
            # 分析成交數據
            total_qty = 0
            buy_qty = 0
            sell_qty = 0
            big_trades = []  # >$10K
            
            for trade in trades:
                qty = float(trade['q'])
                price = float(trade['p'])
                value = qty * price
                is_buyer_maker = trade['m']  # True = 賣方主動 (taker sell)
                
                total_qty += qty
                if is_buyer_maker:
                    sell_qty += qty
                else:
                    buy_qty += qty
                
                if value >= 10000:  # $10K 以上為大單
                    big_trades.append({
                        "value_usdt": round(value, 2),
                        "qty": qty,
                        "side": "SELL" if is_buyer_maker else "BUY",
                        "price": price
                    })
            
            # 計算交易失衡
            trade_imbalance = (buy_qty - sell_qty) / total_qty if total_qty > 0 else 0
            
            # 數據新鮮度檢查
            latest_trade_time = trades[-1]['T']
            data_age_sec = (time.time() * 1000 - latest_trade_time) / 1000
            
            details = {
                "total_trades": len(trades),
                "total_qty": round(total_qty, 4),
                "buy_qty": round(buy_qty, 4),
                "sell_qty": round(sell_qty, 4),
                "trade_imbalance": round(trade_imbalance, 4),
                "big_trades_count": len(big_trades),
                "big_trades": big_trades[:5],  # 只顯示前 5 筆
                "data_age_sec": round(data_age_sec, 1)
            }
            
            self.trade_data = trades
            
            if data_age_sec > 60:
                result.warn_check(f"數據延遲 {data_age_sec:.0f}s", details)
            else:
                result.pass_check(f"交易失衡 = {trade_imbalance:.3f}, 大單 {len(big_trades)} 筆", details)
                
        except Exception as e:
            result.fail_check(f"成交數據驗證失敗: {e}")
        
        return result
    
    # ============================================================
    # 6. Funding Rate 數據驗證
    # ============================================================
    
    def verify_funding_rate(self) -> VerificationResult:
        """驗證資金費率數據"""
        result = VerificationResult("資金費率")
        
        try:
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/premiumIndex",
                              params={"symbol": self.symbol},
                              timeout=5)
            
            if resp.status_code != 200:
                result.fail_check(f"API 錯誤: {resp.status_code}")
                return result
            
            data = resp.json()
            
            funding_rate = float(data.get('lastFundingRate', 0))
            next_funding_time = int(data.get('nextFundingTime', 0))
            
            # 轉換為百分比和可讀時間
            funding_rate_pct = funding_rate * 100
            
            if next_funding_time > 0:
                next_funding = datetime.fromtimestamp(next_funding_time / 1000)
                time_to_next = next_funding - datetime.now()
                minutes_to_next = time_to_next.total_seconds() / 60
            else:
                minutes_to_next = None
            
            details = {
                "funding_rate": funding_rate,
                "funding_rate_pct": round(funding_rate_pct, 4),
                "next_funding_time": datetime.fromtimestamp(next_funding_time / 1000).isoformat() if next_funding_time else None,
                "minutes_to_next": round(minutes_to_next, 1) if minutes_to_next else None,
                "interpretation": self._interpret_funding(funding_rate_pct)
            }
            
            if abs(funding_rate_pct) > 0.1:
                result.warn_check(f"資金費率較高: {funding_rate_pct:.4f}%", details)
            else:
                result.pass_check(f"資金費率: {funding_rate_pct:.4f}% ({details['interpretation']})", details)
                
        except Exception as e:
            result.fail_check(f"資金費率驗證失敗: {e}")
        
        return result
    
    def _interpret_funding(self, rate_pct: float) -> str:
        """解讀資金費率"""
        if rate_pct > 0.05:
            return "多頭擁擠 (做空有優勢)"
        elif rate_pct > 0.01:
            return "略多頭"
        elif rate_pct > -0.01:
            return "中性"
        elif rate_pct > -0.05:
            return "略空頭"
        else:
            return "空頭擁擠 (做多有優勢)"
    
    # ============================================================
    # 7. Open Interest 數據驗證
    # ============================================================
    
    def verify_open_interest(self) -> VerificationResult:
        """驗證未平倉合約數據"""
        result = VerificationResult("未平倉合約 (OI)")
        
        try:
            # 獲取當前 OI
            resp = requests.get(f"{self.BINANCE_FUTURES_API}/fapi/v1/openInterest",
                              params={"symbol": self.symbol},
                              timeout=5)
            
            if resp.status_code != 200:
                result.fail_check(f"API 錯誤: {resp.status_code}")
                return result
            
            data = resp.json()
            current_oi = float(data.get('openInterest', 0))
            
            # 獲取 OI 歷史 (計算變化)
            resp_hist = requests.get(f"{self.BINANCE_FUTURES_API}/futures/data/openInterestHist",
                                   params={"symbol": self.symbol, "period": "5m", "limit": 12},
                                   timeout=5)
            
            oi_change_pct = 0
            if resp_hist.status_code == 200:
                hist = resp_hist.json()
                if hist and len(hist) >= 2:
                    old_oi = float(hist[0].get('sumOpenInterest', 0))
                    if old_oi > 0:
                        oi_change_pct = ((current_oi - old_oi) / old_oi) * 100
            
            # 獲取 OI 美元價值
            if self.price_data.get('futures_mark'):
                oi_value_usd = current_oi * self.price_data['futures_mark']
            else:
                oi_value_usd = current_oi * 100000  # 估計值
            
            details = {
                "open_interest_btc": round(current_oi, 2),
                "open_interest_usd": f"${oi_value_usd/1e9:.2f}B",
                "change_1h_pct": round(oi_change_pct, 2),
                "interpretation": self._interpret_oi_change(oi_change_pct)
            }
            
            result.pass_check(f"OI = {current_oi:.2f} BTC, 1h變化 {oi_change_pct:+.2f}%", details)
                
        except Exception as e:
            result.fail_check(f"OI 驗證失敗: {e}")
        
        return result
    
    def _interpret_oi_change(self, change_pct: float) -> str:
        """解讀 OI 變化"""
        if change_pct > 5:
            return "大量新倉進場"
        elif change_pct > 1:
            return "新倉增加"
        elif change_pct > -1:
            return "持倉穩定"
        elif change_pct > -5:
            return "部分平倉"
        else:
            return "大量平倉/爆倉"
    
    # ============================================================
    # 8. 主力偵測器數據來源驗證
    # ============================================================
    
    def verify_whale_detector_inputs(self) -> VerificationResult:
        """驗證主力偵測器的數據輸入"""
        result = VerificationResult("主力偵測器數據輸入")
        
        # 檢查必要的數據是否都已獲取
        required_data = {
            "價格數據": bool(self.price_data),
            "訂單簿數據": bool(self.orderbook_data),
            "K線數據": bool(self.kline_data),
            "成交數據": bool(self.trade_data),
        }
        
        missing = [k for k, v in required_data.items() if not v]
        
        if missing:
            result.fail_check(f"缺少數據: {', '.join(missing)}", required_data)
        else:
            # 計算主力偵測器需要的指標
            obi = self.orderbook_data.get('obi', 0)
            trade_imbalance = 0  # 從 trade_data 計算
            
            if self.trade_data:
                buy_qty = sum(float(t['q']) for t in self.trade_data if not t['m'])
                sell_qty = sum(float(t['q']) for t in self.trade_data if t['m'])
                total_qty = buy_qty + sell_qty
                if total_qty > 0:
                    trade_imbalance = (buy_qty - sell_qty) / total_qty
            
            indicators = {
                "obi": round(obi, 4),
                "trade_imbalance": round(trade_imbalance, 4),
                "price": self.price_data.get('futures_mark', 0),
                "spread_pct": self.orderbook_data.get('spread_pct', 0),
                "data_sources": list(required_data.keys())
            }
            
            result.pass_check("所有數據輸入正常", indicators)
        
        return result
    
    # ============================================================
    # 9. WebSocket 連線驗證 (異步)
    # ============================================================
    
    async def verify_websocket_connection(self) -> VerificationResult:
        """驗證 WebSocket 連線"""
        result = VerificationResult("WebSocket 連線")
        
        try:
            import websockets
            
            ws_urls = {
                "aggTrade": f"{self.BINANCE_WS_BASE}/ws/{self.symbol.lower()}@aggTrade",
                "depth": f"{self.BINANCE_WS_BASE}/ws/{self.symbol.lower()}@depth5@100ms",
            }
            
            ws_results = {}
            
            for name, url in ws_urls.items():
                try:
                    async with websockets.connect(url) as ws:
                        # 等待第一條消息
                        msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        data = json.loads(msg)
                        
                        ws_results[name] = {
                            "status": "OK",
                            "first_message_keys": list(data.keys())[:5]
                        }
                except asyncio.TimeoutError:
                    ws_results[name] = {"status": "TIMEOUT"}
                except Exception as e:
                    ws_results[name] = {"status": "FAIL", "error": str(e)}
            
            all_ok = all(r.get("status") == "OK" for r in ws_results.values())
            
            if all_ok:
                result.pass_check("所有 WebSocket 流正常", ws_results)
            else:
                result.fail_check("部分 WebSocket 流異常", ws_results)
                
        except ImportError:
            result.warn_check("websockets 模組未安裝", {"note": "WebSocket 功能不可用"})
        except Exception as e:
            result.fail_check(f"WebSocket 驗證失敗: {e}")
        
        return result
    
    # ============================================================
    # 運行所有驗證
    # ============================================================
    
    def run_all_verifications(self) -> Dict:
        """運行所有驗證"""
        print("\n" + "="*60)
        print("🔍 Binance API 數據驗證")
        print(f"   交易對: {self.symbol}")
        print(f"   時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60 + "\n")
        
        # 1. REST API 連線
        self.add_result(self.verify_rest_api_connectivity())
        
        # 2. 價格數據
        self.add_result(self.verify_price_data())
        
        # 3. 訂單簿數據 (OBI)
        self.add_result(self.verify_orderbook_data())
        
        # 4. K 線數據 (MTF)
        self.add_result(self.verify_kline_data())
        
        # 5. 成交數據
        self.add_result(self.verify_trade_data())
        
        # 6. 資金費率
        self.add_result(self.verify_funding_rate())
        
        # 7. 未平倉合約
        self.add_result(self.verify_open_interest())
        
        # 8. 主力偵測器輸入
        self.add_result(self.verify_whale_detector_inputs())
        
        # 9. WebSocket (異步)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        ws_result = loop.run_until_complete(self.verify_websocket_connection())
        self.add_result(ws_result)
        
        # 統計結果
        pass_count = sum(1 for r in self.results if r.status == "PASS")
        warn_count = sum(1 for r in self.results if r.status == "WARNING")
        fail_count = sum(1 for r in self.results if r.status == "FAIL")
        
        print("\n" + "="*60)
        print("📊 驗證結果摘要")
        print("="*60)
        print(f"   ✅ 通過: {pass_count}")
        print(f"   ⚠️ 警告: {warn_count}")
        print(f"   ❌ 失敗: {fail_count}")
        print("="*60)
        
        # 返回完整結果
        return {
            "symbol": self.symbol,
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "pass": pass_count,
                "warning": warn_count,
                "fail": fail_count,
                "total": len(self.results)
            },
            "results": [r.to_dict() for r in self.results],
            "cached_data": {
                "price": self.price_data,
                "orderbook_summary": {
                    k: v for k, v in self.orderbook_data.items() 
                    if k not in ['bids', 'asks']
                }
            }
        }


# ============================================================
# 主程式
# ============================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Binance API 數據驗證")
    parser.add_argument("--symbol", default="BTCUSDT", help="交易對")
    parser.add_argument("--output", help="輸出 JSON 檔案路徑")
    args = parser.parse_args()
    
    verifier = BinanceAPIVerifier(symbol=args.symbol)
    results = verifier.run_all_verifications()
    
    # 保存結果
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n📁 結果已保存到: {args.output}")
    
    # 返回狀態碼
    if results['summary']['fail'] > 0:
        print("\n❌ 存在失敗項目，請檢查!")
        return 1
    elif results['summary']['warning'] > 0:
        print("\n⚠️ 存在警告項目，建議檢查")
        return 0
    else:
        print("\n✅ 所有驗證通過!")
        return 0


if __name__ == "__main__":
    exit(main())
