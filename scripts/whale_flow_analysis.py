"""
巨鯨資金流向分析
通過比對「幣安交易金額」和「鏈上轉帳金額」識別可疑的巨鯨操作

核心邏輯：
1. 大額充值 → 等待 5-30 分鐘 → 大額賣單 = 巨鯨拋售信號（看跌）
2. 大額提現 → 減少賣壓 = 巨鯨囤幣信號（看漲）
3. 金額/時間匹配度計算，識別可能的同一筆交易
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import time
from dataclasses import dataclass

@dataclass
class ChainTransaction:
    """鏈上交易記錄"""
    tx_hash: str
    timestamp: datetime
    amount: float  # BTC
    from_address: str
    to_address: str
    tx_type: str  # 'deposit' or 'withdrawal'
    
@dataclass
class BinanceTrade:
    """幣安交易記錄"""
    trade_id: int
    timestamp: datetime
    price: float
    amount: float  # BTC
    side: str  # 'buy' or 'sell'
    is_buyer_maker: bool  # True = 主動賣, False = 主動買
    
@dataclass
class WhaleSignal:
    """巨鯨信號"""
    signal_type: str  # 'DEPOSIT_SELL' or 'WITHDRAWAL_HOLD'
    chain_tx: ChainTransaction
    related_trades: List[BinanceTrade]
    confidence: float  # 0-1
    amount_matched: bool
    time_matched: bool
    prediction: str  # 'BEARISH' or 'BULLISH'

class WhaleFlowAnalyzer:
    """巨鯨資金流向分析器"""
    
    # 幣安已知地址（範例，實際需要更多地址）
    BINANCE_KNOWN_ADDRESSES = {
        # 冷錢包
        '1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s': 'cold_wallet_1',
        '3Kzh9qAqVWQhEsfQz7zEQL1EuSx5tyNLNS': 'cold_wallet_2',
        'bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h': 'cold_wallet_3',
        
        # 熱錢包（範例）
        '34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo': 'hot_wallet_1',
    }
    
    def __init__(self):
        self.binance_base_url = "https://api.binance.com"
        
    def get_blockchain_transactions(
        self, 
        start_time: datetime, 
        end_time: datetime,
        min_amount: float = 50.0  # 最小追蹤金額 50 BTC
    ) -> List[ChainTransaction]:
        """
        獲取鏈上交易（需要 blockchain.com API 或自建節點）
        
        注意：blockchain.com 有免費 API 但有限制
        生產環境建議使用：
        1. 自建 Bitcoin Core 節點
        2. Blockchair API ($29/月)
        3. Blockchain.com Premium API
        """
        print(f"📡 獲取鏈上交易數據 {start_time} ~ {end_time}")
        
        transactions = []
        
        # 方法1: 使用 blockchain.com API（免費但有限制）
        for address, label in self.BINANCE_KNOWN_ADDRESSES.items():
            try:
                # 獲取地址的交易記錄
                url = f"https://blockchain.info/rawaddr/{address}"
                response = requests.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    for tx in data.get('txs', []):
                        tx_time = datetime.fromtimestamp(tx['time'])
                        
                        # 檢查時間範圍
                        if not (start_time <= tx_time <= end_time):
                            continue
                        
                        # 計算金額（satoshi -> BTC）
                        amount = 0
                        tx_type = None
                        from_addr = None
                        to_addr = None
                        
                        # 檢查是充值還是提現
                        for output in tx['out']:
                            if output.get('addr') == address:
                                # 這是充值到幣安
                                amount = output['value'] / 1e8  # satoshi to BTC
                                tx_type = 'deposit'
                                to_addr = address
                                # from_addr 可能有多個，取第一個
                                if tx['inputs']:
                                    from_addr = tx['inputs'][0].get('prev_out', {}).get('addr', 'unknown')
                                break
                        
                        if not tx_type:
                            for input_tx in tx['inputs']:
                                if input_tx.get('prev_out', {}).get('addr') == address:
                                    # 這是從幣安提現
                                    amount = input_tx['prev_out']['value'] / 1e8
                                    tx_type = 'withdrawal'
                                    from_addr = address
                                    # to_addr 可能有多個，取第一個
                                    if tx['out']:
                                        to_addr = tx['out'][0].get('addr', 'unknown')
                                    break
                        
                        # 過濾小額交易
                        if amount < min_amount:
                            continue
                        
                        transactions.append(ChainTransaction(
                            tx_hash=tx['hash'],
                            timestamp=tx_time,
                            amount=amount,
                            from_address=from_addr or 'unknown',
                            to_address=to_addr or 'unknown',
                            tx_type=tx_type or 'unknown'
                        ))
                
                # 避免 API 限制
                time.sleep(1)
                
            except Exception as e:
                print(f"⚠️ 獲取地址 {address} 數據失敗: {e}")
                continue
        
        print(f"✅ 獲取到 {len(transactions)} 筆大額鏈上交易")
        return transactions
    
    def get_binance_large_trades(
        self,
        start_time: datetime,
        end_time: datetime,
        min_amount: float = 10.0  # 最小追蹤金額 10 BTC
    ) -> List[BinanceTrade]:
        """
        獲取幣安大單交易記錄
        """
        print(f"📡 獲取幣安交易數據 {start_time} ~ {end_time}")
        
        trades = []
        
        # 使用 aggTrades API
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)
        
        url = f"{self.binance_base_url}/api/v3/aggTrades"
        params = {
            'symbol': 'BTCUSDT',
            'startTime': start_ts,
            'endTime': end_ts,
            'limit': 1000
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                
                for trade in data:
                    amount = float(trade['q'])  # 數量（BTC）
                    
                    # 過濾小額交易
                    if amount < min_amount:
                        continue
                    
                    trades.append(BinanceTrade(
                        trade_id=trade['a'],
                        timestamp=datetime.fromtimestamp(trade['T'] / 1000),
                        price=float(trade['p']),
                        amount=amount,
                        side='sell' if trade['m'] else 'buy',  # m=True 表示 maker 是賣方
                        is_buyer_maker=trade['m']
                    ))
                
                print(f"✅ 獲取到 {len(trades)} 筆幣安大單")
            else:
                print(f"⚠️ 幣安 API 錯誤: {response.status_code}")
                
        except Exception as e:
            print(f"❌ 獲取幣安數據失敗: {e}")
        
        return trades
    
    def match_transactions(
        self,
        chain_tx: ChainTransaction,
        binance_trades: List[BinanceTrade],
        time_window_minutes: int = 30,
        amount_tolerance: float = 0.05  # 5% 誤差
    ) -> Tuple[List[BinanceTrade], float]:
        """
        匹配鏈上交易和幣安交易
        
        Returns:
            (匹配的交易列表, 匹配信心分數)
        """
        matched_trades = []
        
        # 時間窗口
        time_start = chain_tx.timestamp
        time_end = chain_tx.timestamp + timedelta(minutes=time_window_minutes)
        
        for trade in binance_trades:
            # 檢查時間
            if not (time_start <= trade.timestamp <= time_end):
                continue
            
            # 檢查金額（允許誤差）
            amount_diff = abs(trade.amount - chain_tx.amount)
            amount_match = amount_diff / chain_tx.amount <= amount_tolerance
            
            # 檢查方向
            direction_match = False
            if chain_tx.tx_type == 'deposit':
                # 充值通常對應賣出
                direction_match = (trade.side == 'sell')
            elif chain_tx.tx_type == 'withdrawal':
                # 提現通常對應買入（或不交易）
                direction_match = (trade.side == 'buy')
            
            if amount_match and direction_match:
                matched_trades.append(trade)
        
        # 計算信心分數
        confidence = 0.0
        if matched_trades:
            # 時間越近，信心越高
            min_time_diff = min([
                abs((trade.timestamp - chain_tx.timestamp).total_seconds()) 
                for trade in matched_trades
            ])
            time_score = max(0, 1 - min_time_diff / (time_window_minutes * 60))
            
            # 金額越接近，信心越高
            best_match = min(matched_trades, key=lambda t: abs(t.amount - chain_tx.amount))
            amount_score = 1 - abs(best_match.amount - chain_tx.amount) / chain_tx.amount
            
            confidence = (time_score * 0.4 + amount_score * 0.6)
        
        return matched_trades, confidence
    
    def generate_whale_signals(
        self,
        start_time: datetime,
        end_time: datetime,
        min_chain_amount: float = 50.0,
        min_binance_amount: float = 10.0,
        min_confidence: float = 0.5
    ) -> List[WhaleSignal]:
        """
        生成巨鯨信號
        """
        print("="*70)
        print("🐋 巨鯨資金流向分析")
        print("="*70)
        
        # 1. 獲取鏈上交易
        chain_txs = self.get_blockchain_transactions(
            start_time, end_time, min_chain_amount
        )
        
        # 2. 獲取幣安交易
        binance_trades = self.get_binance_large_trades(
            start_time, end_time, min_binance_amount
        )
        
        # 3. 匹配並生成信號
        signals = []
        
        for chain_tx in chain_txs:
            # 匹配交易
            matched_trades, confidence = self.match_transactions(
                chain_tx, binance_trades
            )
            
            # 過濾低信心信號
            if confidence < min_confidence:
                continue
            
            # 生成信號
            signal_type = None
            prediction = None
            
            if chain_tx.tx_type == 'deposit' and matched_trades:
                # 充值後賣出 = 看跌
                signal_type = 'DEPOSIT_SELL'
                prediction = 'BEARISH'
            elif chain_tx.tx_type == 'withdrawal':
                # 提現 = 囤幣 = 看漲
                signal_type = 'WITHDRAWAL_HOLD'
                prediction = 'BULLISH'
            
            if signal_type:
                signals.append(WhaleSignal(
                    signal_type=signal_type,
                    chain_tx=chain_tx,
                    related_trades=matched_trades,
                    confidence=confidence,
                    amount_matched=len(matched_trades) > 0,
                    time_matched=True,
                    prediction=prediction
                ))
        
        print(f"\n✅ 生成 {len(signals)} 個巨鯨信號")
        return signals
    
    def print_signals(self, signals: List[WhaleSignal]):
        """打印信號"""
        print("\n" + "="*70)
        print("📊 巨鯨信號分析結果")
        print("="*70)
        
        for i, signal in enumerate(signals, 1):
            print(f"\n信號 #{i}")
            print(f"  類型: {signal.signal_type}")
            print(f"  預測: {'🔴 看跌 (BEARISH)' if signal.prediction == 'BEARISH' else '🟢 看漲 (BULLISH)'}")
            print(f"  信心: {signal.confidence:.2%}")
            print(f"  鏈上交易:")
            print(f"    時間: {signal.chain_tx.timestamp}")
            print(f"    金額: {signal.chain_tx.amount:.2f} BTC")
            print(f"    類型: {signal.chain_tx.tx_type}")
            print(f"    交易哈希: {signal.chain_tx.tx_hash[:16]}...")
            
            if signal.related_trades:
                print(f"  匹配的幣安交易: {len(signal.related_trades)} 筆")
                for trade in signal.related_trades[:3]:  # 只顯示前3筆
                    print(f"    - {trade.timestamp}: {trade.side.upper()} {trade.amount:.2f} BTC @ ${trade.price:,.2f}")
    
    def backtest_signals(
        self,
        signals: List[WhaleSignal],
        price_data: pd.DataFrame
    ) -> Dict:
        """
        回測巨鯨信號效果
        
        Args:
            signals: 巨鯨信號列表
            price_data: 價格數據 (columns: timestamp, close)
        """
        print("\n" + "="*70)
        print("📈 回測巨鯨信號效果")
        print("="*70)
        
        results = {
            'total_signals': len(signals),
            'bearish_signals': 0,
            'bullish_signals': 0,
            'bearish_correct': 0,
            'bullish_correct': 0,
            'avg_price_change_1h': 0,
            'avg_price_change_24h': 0
        }
        
        price_changes_1h = []
        price_changes_24h = []
        
        for signal in signals:
            # 獲取信號時的價格
            signal_time = signal.chain_tx.timestamp
            price_at_signal = price_data[
                price_data['timestamp'] <= signal_time
            ]['close'].iloc[-1]
            
            # 1小時後價格
            time_1h = signal_time + timedelta(hours=1)
            price_1h_data = price_data[price_data['timestamp'] >= time_1h]
            if not price_1h_data.empty:
                price_1h = price_1h_data['close'].iloc[0]
                change_1h = (price_1h - price_at_signal) / price_at_signal
                price_changes_1h.append(change_1h)
            
            # 24小時後價格
            time_24h = signal_time + timedelta(hours=24)
            price_24h_data = price_data[price_data['timestamp'] >= time_24h]
            if not price_24h_data.empty:
                price_24h = price_24h_data['close'].iloc[0]
                change_24h = (price_24h - price_at_signal) / price_at_signal
                price_changes_24h.append(change_24h)
                
                # 檢查預測是否正確
                if signal.prediction == 'BEARISH':
                    results['bearish_signals'] += 1
                    if change_24h < -0.01:  # 下跌超過1%
                        results['bearish_correct'] += 1
                elif signal.prediction == 'BULLISH':
                    results['bullish_signals'] += 1
                    if change_24h > 0.01:  # 上漲超過1%
                        results['bullish_correct'] += 1
        
        # 計算平均價格變化
        if price_changes_1h:
            results['avg_price_change_1h'] = np.mean(price_changes_1h)
        if price_changes_24h:
            results['avg_price_change_24h'] = np.mean(price_changes_24h)
        
        # 計算準確率
        bearish_accuracy = (
            results['bearish_correct'] / results['bearish_signals']
            if results['bearish_signals'] > 0 else 0
        )
        bullish_accuracy = (
            results['bullish_correct'] / results['bullish_signals']
            if results['bullish_signals'] > 0 else 0
        )
        
        print(f"\n總信號數: {results['total_signals']}")
        print(f"\n看跌信號 (BEARISH):")
        print(f"  數量: {results['bearish_signals']}")
        print(f"  準確: {results['bearish_correct']} ({bearish_accuracy:.1%})")
        print(f"\n看漲信號 (BULLISH):")
        print(f"  數量: {results['bullish_signals']}")
        print(f"  準確: {results['bullish_correct']} ({bullish_accuracy:.1%})")
        print(f"\n平均價格變化:")
        print(f"  1小時: {results['avg_price_change_1h']:+.2%}")
        print(f"  24小時: {results['avg_price_change_24h']:+.2%}")
        
        return results

def main():
    """測試範例"""
    analyzer = WhaleFlowAnalyzer()
    
    # 測試時間範圍（最近7天）
    end_time = datetime.now()
    start_time = end_time - timedelta(days=7)
    
    print(f"分析時間範圍: {start_time} ~ {end_time}")
    print()
    
    # 生成信號
    signals = analyzer.generate_whale_signals(
        start_time=start_time,
        end_time=end_time,
        min_chain_amount=50.0,  # 最小50 BTC鏈上交易
        min_binance_amount=10.0,  # 最小10 BTC幣安交易
        min_confidence=0.5  # 最小50%信心
    )
    
    # 打印信號
    analyzer.print_signals(signals)
    
    # 如果有歷史價格數據，可以回測
    # df = pd.read_parquet('data/historical/BTCUSDT_15m.parquet')
    # df['timestamp'] = pd.to_datetime(df['timestamp'])
    # results = analyzer.backtest_signals(signals, df)

if __name__ == '__main__':
    main()
