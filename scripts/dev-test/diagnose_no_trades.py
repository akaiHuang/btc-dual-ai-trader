"""
診斷模式 - 無交易原因分析器

功能：
1. 記錄一段時間內所有決策
2. 統計信號分布、風險等級分布
3. 分析阻擋原因（為何沒有交易）
4. 輸出詳細診斷報告和建議

使用方式：
    python scripts/diagnose_no_trades.py [minutes]
    
範例：
    python scripts/diagnose_no_trades.py 5  # 監控5分鐘
    python scripts/diagnose_no_trades.py 30 # 監控30分鐘
"""

import asyncio
import sys
from datetime import datetime
from collections import defaultdict, Counter
import json
from pathlib import Path
import websockets

# 添加 src 到路徑
sys.path.append(str(Path(__file__).parent.parent))

from src.exchange.obi_calculator import OBICalculator
from src.exchange.vpin_calculator import VPINCalculator
from src.exchange.signed_volume_tracker import SignedVolumeTracker
from src.exchange.spread_depth_monitor import SpreadDepthMonitor
from src.strategy.layered_trading_engine import LayeredTradingEngine


class NoTradesDiagnostic:
    """無交易診斷器"""
    
    def __init__(self, symbol: str = "BTCUSDT", duration_minutes: int = 5):
        self.symbol = symbol.upper()
        self.duration_minutes = duration_minutes
        
        # 初始化組件
        self.obi_calc = OBICalculator(symbol=symbol)
        self.vpin_calc = VPINCalculator(symbol=symbol, bucket_size=10000, num_buckets=20)  # 降低 bucket 大小
        self.volume_tracker = SignedVolumeTracker(symbol=symbol)
        self.spread_monitor = SpreadDepthMonitor(symbol=symbol)
        
        # 使用寬鬆參數的交易引擎
        self.trading_engine = LayeredTradingEngine(
            signal_config={
                'long_threshold': 0.4,
                'short_threshold': 0.4
            },
            regime_config={
                'vpin_threshold': 0.65,
            }
        )
        
        # 記錄數據
        self.decisions = []
        self.latest_price = 0.0
        self.latest_orderbook = None
        self.orderbook_timestamp = 0
        self.warmup_trades = 0
        self.warmup_required = 50
        
        # WebSocket 連接
        self.ws = None
        self.running = True
        
        # 統計計數器
        self.signal_counter = Counter()
        self.risk_counter = Counter()
        self.block_reasons = defaultdict(int)
        self.confidence_bins = defaultdict(int)
        self.vpin_bins = defaultdict(int)
        
    async def run(self):
        """運行診斷"""
        print("=" * 80)
        print(f"🔍 無交易診斷模式")
        print("=" * 80)
        print(f"📊 交易對: {self.symbol}")
        print(f"⏰ 監控時長: {self.duration_minutes} 分鐘")
        print(f"🎯 目的: 分析為何沒有交易發生")
        print("=" * 80)
        print()
        
        # 連接 WebSocket
        print("🔌 連接 Binance WebSocket...")
        
        # 創建並發任務
        tasks = [
            self._websocket_handler(),
            self._decision_loop()
        ]
        
        await asyncio.gather(*tasks)
    
    async def _websocket_handler(self):
        """WebSocket 處理器"""
        uri = f"wss://fstream.binance.com/stream?streams={self.symbol.lower()}@depth@100ms/{self.symbol.lower()}@aggTrade"
        
        async for websocket in websockets.connect(uri):
            try:
                self.ws = websocket
                print("✅ WebSocket 已連接")
                print(f"📥 收集數據中（需要至少 {self.warmup_required} 筆交易熱身）...")
                print()
                
                async for message in websocket:
                    if not self.running:
                        break
                    
                    try:
                        data = json.loads(message)
                        await self._process_message(data)
                    except Exception as e:
                        pass  # 忽略處理錯誤
                        
            except websockets.ConnectionClosed:
                if self.running:
                    await asyncio.sleep(1)  # 重連延遲
                else:
                    break
            except Exception:
                if self.running:
                    await asyncio.sleep(1)
                else:
                    break
    
    async def _process_message(self, data: dict):
        """處理 WebSocket 消息"""
        if 'stream' not in data:
            return
        
        stream = data['stream']
        msg_data = data['data']
        
        if 'depth' in stream:
            # 訂單簿更新
            self._handle_orderbook(msg_data)
        elif 'aggTrade' in stream:
            # 交易更新
            self._handle_trade(msg_data)
    
    async def _decision_loop(self):
        """決策循環"""
        # 等待 WebSocket 連接
        while self.ws is None:
            await asyncio.sleep(0.1)
        
        # 等待熱身
        while self.warmup_trades < self.warmup_required:
            await asyncio.sleep(0.5)
        
        print(f"✅ 數據熱身完成（{self.warmup_trades} 筆交易）")
        print()
        print("=" * 80)
        print("🚀 開始記錄決策...")
        print("=" * 80)
        print()
        
        # 開始決策記錄
        start_time = datetime.now()
        decision_count = 0
        
        while (datetime.now() - start_time).total_seconds() < self.duration_minutes * 60:
            await asyncio.sleep(15)  # 每15秒決策一次
            
            # 做決策
            decision = self._make_decision()
            if decision:
                decision_count += 1
                self.decisions.append(decision)
                
                # 簡單打印進度
                print(f"[{decision['timestamp'].strftime('%H:%M:%S')}] "
                      f"決策 #{decision_count}: "
                      f"{decision['signal']['direction']}, "
                      f"信心 {decision['signal']['confidence']:.3f}, "
                      f"風險 {decision['regime']['risk_level']}, "
                      f"可交易: {'✅' if decision['can_trade'] else '❌'}")
        
        print()
        print("=" * 80)
        print("✅ 數據收集完成")
        print("=" * 80)
        print()
        
        # 生成報告
        await self._generate_report()
        
        # 停止 WebSocket
        self.running = False
        if self.ws:
            await self.ws.close()
    
    def _handle_orderbook(self, data: dict):
        """處理訂單簿數據"""
        try:
            bids = [[float(p), float(q)] for p, q in data['b'][:20]]
            asks = [[float(p), float(q)] for p, q in data['a'][:20]]
            
            orderbook_data = {
                'bids': bids,
                'asks': asks,
                'E': data.get('E', 0)
            }
            
            self.latest_orderbook = orderbook_data
            self.orderbook_timestamp = data.get('E', 0)
            
            # 更新 OBI（傳入 bids 和 asks，不是字典）
            self.obi_calc.update_orderbook(bids, asks)
            
            # 更新 Spread & Depth（傳入完整字典）
            self.spread_monitor.update_orderbook(orderbook_data)
        except Exception as e:
            pass  # 忽略錯誤
    
    def _handle_trade(self, data: dict):
        """處理交易數據"""
        try:
            self.latest_price = float(data['p'])
            self.warmup_trades += 1
            
            # 更新 VPIN
            self.vpin_calc.process_trade(data)
            
            # 更新 Signed Volume
            self.volume_tracker.process_trade(data)
        except Exception:
            pass
    
    def _make_decision(self) -> dict:
        """做決策並記錄"""
        try:
            # 獲取最新指標
            obi_data = self.obi_calc.get_current_obi()
            if obi_data is None:
                print("  ⚠️ OBI 數據未就緒")
                return None
            
            obi = obi_data['obi']
            obi_velocity = self.obi_calc.calculate_obi_velocity()
            vpin = self.vpin_calc.get_current_vpin()
            
            if vpin is None:
                # VPIN 未準備好，使用默認值
                print("  ⚠️ VPIN 數據未就緒，使用默認值 0.3")
                vpin = 0.3
            
            signed_volume = self.volume_tracker.get_net_volume(window_size=100)
            
            # 獲取 Spread & Depth 數據
            if self.latest_orderbook is None:
                print("  ⚠️ 訂單簿數據未就緒")
                return None
            
            bids = self.latest_orderbook.get('bids', [])
            asks = self.latest_orderbook.get('asks', [])
            
            if not bids or not asks:
                print("  ⚠️ 訂單簿為空")
                return None
            
            # 計算 Spread & Depth
            spread_data = self.spread_monitor.calculate_spread(bids, asks)
            depth_data = self.spread_monitor.calculate_depth(bids, asks, levels=5)
            depth_imbalance = self.spread_monitor.calculate_depth_imbalance(bids, asks, levels=5)
            
            # 準備市場數據
            market_data = {
                'timestamp': datetime.now().timestamp() * 1000,
                'price': self.latest_price,
                'obi': obi,
                'obi_velocity': obi_velocity if obi_velocity is not None else 0.0,
                'signed_volume': signed_volume,
                'microprice_pressure': 0.0,  # 簡化
                'vpin': vpin,
                'spread_bps': spread_data.get('spread_bps', 0.0),
                'total_depth': depth_data.get('total_depth_btc', 0.0),
                'depth_imbalance': depth_imbalance
            }
            
            # 使用交易引擎做決策
            decision = self.trading_engine.process_market_data(market_data)
            
            # 增加時間戳
            decision['timestamp'] = datetime.now()
            
            # 更新統計
            self._update_statistics(decision)
            
            return decision
            
        except Exception as e:
            print(f"  ❌ 決策錯誤: {e}")
            return None
    
    def _update_statistics(self, decision: dict):
        """更新統計計數器"""
        # 信號分布
        signal = decision['signal']['direction']
        self.signal_counter[signal] += 1
        
        # 風險等級分布
        risk = decision['regime']['risk_level']
        self.risk_counter[risk] += 1
        
        # 信心度分箱
        confidence = decision['signal']['confidence']
        if confidence < 0.3:
            self.confidence_bins['< 0.3'] += 1
        elif confidence < 0.4:
            self.confidence_bins['0.3-0.4'] += 1
        elif confidence < 0.5:
            self.confidence_bins['0.4-0.5'] += 1
        elif confidence < 0.6:
            self.confidence_bins['0.5-0.6'] += 1
        else:
            self.confidence_bins['>= 0.6'] += 1
        
        # VPIN 分箱
        vpin = decision['regime']['details']['checks']['vpin']['value']
        if vpin is not None:
            if vpin < 0.3:
                self.vpin_bins['< 0.3 (SAFE)'] += 1
            elif vpin < 0.5:
                self.vpin_bins['0.3-0.5 (WARNING)'] += 1
            elif vpin < 0.65:
                self.vpin_bins['0.5-0.65 (WARNING+)'] += 1
            elif vpin < 0.7:
                self.vpin_bins['0.65-0.7 (DANGER)'] += 1
            else:
                self.vpin_bins['>= 0.7 (CRITICAL)'] += 1
        
        # 阻擋原因
        if not decision['can_trade']:
            reasons = decision['regime']['blocked_reasons']
            for reason in reasons:
                if 'VPIN' in reason:
                    self.block_reasons['VPIN 過高'] += 1
                elif 'spread' in reason or '價差' in reason:
                    self.block_reasons['Spread 過寬'] += 1
                elif 'depth' in reason or '深度' in reason:
                    self.block_reasons['Depth 不足'] += 1
    
    async def _generate_report(self):
        """生成診斷報告"""
        total_decisions = len(self.decisions)
        tradeable_count = sum(1 for d in self.decisions if d['can_trade'])
        
        print("┌" + "─" * 78 + "┐")
        print("│" + " " * 28 + "📋 診斷報告" + " " * 38 + "│")
        print("└" + "─" * 78 + "┘")
        print()
        
        # 基本統計
        print("📊 基本統計")
        print("─" * 80)
        print(f"   總決策次數: {total_decisions}")
        print(f"   可交易決策: {tradeable_count} ({tradeable_count/total_decisions*100:.1f}%)")
        print(f"   被阻擋決策: {total_decisions - tradeable_count} ({(total_decisions-tradeable_count)/total_decisions*100:.1f}%)")
        print()
        
        # 信號分布
        print("🎯 信號分布")
        print("─" * 80)
        for signal, count in sorted(self.signal_counter.items()):
            emoji = "📈" if signal == "LONG" else "📉" if signal == "SHORT" else "⚖️"
            bar = "█" * int(count / total_decisions * 50)
            print(f"   {emoji} {signal:8s}: {count:3d} ({count/total_decisions*100:5.1f}%) {bar}")
        print()
        
        # 風險等級分布
        print("🔒 風險等級分布")
        print("─" * 80)
        risk_emoji = {
            'SAFE': '🟢',
            'WARNING': '🟡',
            'DANGER': '🟠',
            'CRITICAL': '🔴'
        }
        for risk, count in sorted(self.risk_counter.items(), 
                                   key=lambda x: ['SAFE', 'WARNING', 'DANGER', 'CRITICAL'].index(x[0])):
            emoji = risk_emoji.get(risk, '⚪')
            bar = "█" * int(count / total_decisions * 50)
            print(f"   {emoji} {risk:10s}: {count:3d} ({count/total_decisions*100:5.1f}%) {bar}")
        print()
        
        # 信心度分布
        print("💪 信心度分布")
        print("─" * 80)
        for bin_range in ['< 0.3', '0.3-0.4', '0.4-0.5', '0.5-0.6', '>= 0.6']:
            count = self.confidence_bins[bin_range]
            if count > 0:
                bar = "█" * int(count / total_decisions * 50)
                print(f"   {bin_range:10s}: {count:3d} ({count/total_decisions*100:5.1f}%) {bar}")
        print()
        
        # VPIN 分布
        print("☠️  VPIN 分布")
        print("─" * 80)
        for bin_range in ['< 0.3 (SAFE)', '0.3-0.5 (WARNING)', '0.5-0.65 (WARNING+)', 
                          '0.65-0.7 (DANGER)', '>= 0.7 (CRITICAL)']:
            count = self.vpin_bins[bin_range]
            if count > 0:
                bar = "█" * int(count / total_decisions * 50)
                print(f"   {bin_range:20s}: {count:3d} ({count/total_decisions*100:5.1f}%) {bar}")
        print()
        
        # 阻擋原因
        if self.block_reasons:
            print("🚫 阻擋原因統計")
            print("─" * 80)
            for reason, count in sorted(self.block_reasons.items(), key=lambda x: -x[1]):
                bar = "█" * int(count / (total_decisions - tradeable_count) * 50)
                print(f"   {reason:20s}: {count:3d} ({count/(total_decisions-tradeable_count)*100:5.1f}%) {bar}")
            print()
        
        # 診斷結論
        print("┌" + "─" * 78 + "┐")
        print("│" + " " * 28 + "🔍 診斷結論" + " " * 38 + "│")
        print("└" + "─" * 78 + "┘")
        print()
        
        self._print_diagnosis()
        
        # 保存詳細數據
        self._save_data()
    
    def _print_diagnosis(self):
        """打印診斷結論和建議"""
        total = len(self.decisions)
        tradeable = sum(1 for d in self.decisions if d['can_trade'])
        
        # 問題 1: 是否有交易機會？
        if tradeable == 0:
            print("❌ 問題診斷：沒有任何可交易機會")
            print()
            
            # 分析主要原因
            neutral_pct = self.signal_counter.get('NEUTRAL', 0) / total * 100
            critical_pct = self.risk_counter.get('CRITICAL', 0) / total * 100
            danger_pct = self.risk_counter.get('DANGER', 0) / total * 100
            
            low_confidence_pct = sum(self.confidence_bins.get(k, 0) for k in ['< 0.3', '0.3-0.4']) / total * 100
            high_vpin_pct = sum(self.vpin_bins.get(k, 0) for k in ['0.65-0.7 (DANGER)', '>= 0.7 (CRITICAL)']) / total * 100
            
            if neutral_pct > 80:
                print(f"📌 主因 1: 信號太弱（{neutral_pct:.0f}% 為 NEUTRAL）")
                print(f"   → {low_confidence_pct:.0f}% 的信心度 < 0.4")
                print(f"   → 當前閾值: 0.4 (已經很寬鬆)")
                print()
                print("💡 建議：")
                print("   1. 市場可能處於橫盤整理，缺乏明確趨勢")
                print("   2. 可以等待波動加大的時段（如美股開盤）")
                print("   3. 或考慮使用更激進的參數（signal_threshold = 0.3）")
                print()
            
            if high_vpin_pct > 50:
                print(f"📌 主因 2: VPIN 過高（{high_vpin_pct:.0f}% >= 0.65）")
                print(f"   → {self.vpin_bins.get('>= 0.7 (CRITICAL)', 0)} 次達到 CRITICAL (>= 0.7)")
                print()
                print("💡 建議：")
                print("   1. 當前市場知情交易者活躍（高風險）")
                print("   2. 建議等待 VPIN 降至 0.5 以下再交易")
                print("   3. 這是保護機制，避免 Flash Crash 損失")
                print()
            
            if critical_pct + danger_pct > 70:
                print(f"📌 主因 3: 風險等級過高（{critical_pct + danger_pct:.0f}% DANGER/CRITICAL）")
                print()
                if 'VPIN 過高' in self.block_reasons:
                    print(f"   → VPIN 阻擋: {self.block_reasons['VPIN 過高']} 次")
                if 'Spread 過寬' in self.block_reasons:
                    print(f"   → Spread 阻擋: {self.block_reasons['Spread 過寬']} 次")
                if 'Depth 不足' in self.block_reasons:
                    print(f"   → Depth 阻擋: {self.block_reasons['Depth 不足']} 次")
                print()
                print("💡 建議：")
                print("   1. 當前市場環境不適合交易（流動性或風險問題）")
                print("   2. 等待市場狀況改善")
                print()
        
        elif tradeable < total * 0.1:
            print(f"⚠️  問題診斷：交易機會很少（僅 {tradeable/total*100:.1f}%）")
            print()
            print("💡 建議：")
            print("   1. 市場條件接近可交易邊緣")
            print("   2. 可以略微放寬參數或等待更好時機")
            print()
        
        else:
            print(f"✅ 診斷：有交易機會（{tradeable/total*100:.1f}%）")
            print()
            print("💡 說明：")
            print("   系統運作正常，有足夠的交易機會")
            print("   如果實際沒有執行交易，請檢查執行層邏輯")
            print()
    
    def _save_data(self):
        """保存詳細數據到文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"data/diagnosis_{timestamp}.json"
        
        data = {
            'metadata': {
                'symbol': self.symbol,
                'duration_minutes': self.duration_minutes,
                'total_decisions': len(self.decisions),
                'timestamp': timestamp
            },
            'statistics': {
                'signals': dict(self.signal_counter),
                'risk_levels': dict(self.risk_counter),
                'confidence_bins': dict(self.confidence_bins),
                'vpin_bins': dict(self.vpin_bins),
                'block_reasons': dict(self.block_reasons)
            },
            'decisions': [
                {
                    'timestamp': d['timestamp'].isoformat(),
                    'signal': d['signal']['direction'],
                    'confidence': d['signal']['confidence'],
                    'risk_level': d['regime']['risk_level'],
                    'can_trade': d['can_trade'],
                    'blocked_reasons': d['regime']['blocked_reasons']
                }
                for d in self.decisions
            ]
        }
        
        Path(filename).parent.mkdir(exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"📁 詳細數據已保存: {filename}")
        print()


async def main():
    """主函數"""
    # 獲取參數
    duration_minutes = 5
    if len(sys.argv) > 1:
        try:
            duration_minutes = int(sys.argv[1])
        except ValueError:
            print("❌ 參數錯誤：請提供分鐘數（整數）")
            print("使用方式: python diagnose_no_trades.py [minutes]")
            sys.exit(1)
    
    # 運行診斷
    diagnostic = NoTradesDiagnostic(duration_minutes=duration_minutes)
    await diagnostic.run()


if __name__ == "__main__":
    asyncio.run(main())
