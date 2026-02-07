"""
持倉時間實驗台 - Holding Time Sweeper
用數據找出最佳持倉時間和 TP/SL 組合

作者: Phase 2 Week 5
日期: 2025-11-14
"""

from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """單筆交易結果"""
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    direction: str  # LONG or SHORT
    
    # 實驗參數
    holding_time_seconds: int
    take_profit_pct: float
    stop_loss_pct: float
    
    # 結果指標
    pnl: float
    pnl_pct: float
    is_win: bool
    exit_reason: str  # TP, SL, TIME, FORCED
    
    # 市場狀態
    vpin_at_entry: Optional[float] = None
    spread_at_entry: Optional[float] = None
    volume_at_entry: Optional[float] = None


@dataclass
class ExperimentResult:
    """實驗結果"""
    holding_time_seconds: int
    take_profit_pct: float
    stop_loss_pct: float
    
    # 績效指標
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # 盈虧統計
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0  # 總盈利 / 總虧損
    
    # 風險指標
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    
    # 出場原因統計
    tp_exits: int = 0
    sl_exits: int = 0
    time_exits: int = 0
    forced_exits: int = 0
    
    # 詳細記錄
    trades: List[TradeResult] = field(default_factory=list)


class HoldingTimeSweeper:
    """
    持倉時間實驗台
    
    功能：
    1. 掃描不同持倉時間（30s ~ 1h）
    2. 掃描不同 TP/SL 組合
    3. 計算每組參數的績效指標
    4. 找出最優參數組合
    """
    
    def __init__(
        self,
        holding_times: List[int] = None,
        tp_levels: List[float] = None,
        sl_levels: List[float] = None,
        output_dir: str = "data/experiments"
    ):
        """
        初始化實驗台
        
        Args:
            holding_times: 持倉時間列表（秒），默認 [30, 60, 180, 300, 600, 1800, 3600]
            tp_levels: 止盈百分比列表，默認 [0.002, 0.003, 0.004, 0.005, 0.008, 0.010]
            sl_levels: 止損百分比列表，默認 [0.001, 0.0015, 0.002, 0.0025, 0.003]
            output_dir: 結果輸出目錄
        """
        # 默認參數範圍
        self.holding_times = holding_times or [
            30,    # 30 秒
            60,    # 1 分鐘
            180,   # 3 分鐘
            300,   # 5 分鐘
            600,   # 10 分鐘
            1800,  # 30 分鐘
            3600   # 1 小時
        ]
        
        self.tp_levels = tp_levels or [
            0.002,  # 0.2%
            0.003,  # 0.3%
            0.004,  # 0.4%
            0.005,  # 0.5%
            0.008,  # 0.8%
            0.010   # 1.0%
        ]
        
        self.sl_levels = sl_levels or [
            0.001,  # 0.1%
            0.0015, # 0.15%
            0.002,  # 0.2%
            0.0025, # 0.25%
            0.003   # 0.3%
        ]
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 存儲所有實驗結果
        self.results: Dict[Tuple[int, float, float], ExperimentResult] = {}
        
        logger.info(
            f"持倉時間實驗台初始化完成\n"
            f"  持倉時間: {len(self.holding_times)} 種\n"
            f"  止盈等級: {len(self.tp_levels)} 種\n"
            f"  止損等級: {len(self.sl_levels)} 種\n"
            f"  總實驗數: {len(self.holding_times) * len(self.tp_levels) * len(self.sl_levels)}"
        )
    
    def simulate_trade(
        self,
        entry_time: datetime,
        entry_price: float,
        direction: str,
        price_data: List[Tuple[datetime, float]],  # [(time, price), ...]
        holding_time_seconds: int,
        take_profit_pct: float,
        stop_loss_pct: float,
        vpin: Optional[float] = None,
        spread: Optional[float] = None,
        volume: Optional[float] = None
    ) -> TradeResult:
        """
        模擬單筆交易
        
        Args:
            entry_time: 進場時間
            entry_price: 進場價格
            direction: 方向 (LONG/SHORT)
            price_data: 後續價格數據 [(時間, 價格), ...]
            holding_time_seconds: 最大持倉時間
            take_profit_pct: 止盈百分比
            stop_loss_pct: 止損百分比
            vpin: 進場時 VPIN
            spread: 進場時 Spread
            volume: 進場時成交量
            
        Returns:
            TradeResult
        """
        # 計算止盈止損價格
        if direction == "LONG":
            tp_price = entry_price * (1 + take_profit_pct)
            sl_price = entry_price * (1 - stop_loss_pct)
        else:  # SHORT
            tp_price = entry_price * (1 - take_profit_pct)
            sl_price = entry_price * (1 + stop_loss_pct)
        
        # 最大持倉時間
        max_exit_time = entry_time + timedelta(seconds=holding_time_seconds)
        
        # 模擬價格走勢
        exit_time = entry_time
        exit_price = entry_price
        exit_reason = "TIME"
        
        for time, price in price_data:
            if time <= entry_time:
                continue
            
            # 檢查止盈
            if direction == "LONG" and price >= tp_price:
                exit_time = time
                exit_price = tp_price
                exit_reason = "TP"
                break
            elif direction == "SHORT" and price <= tp_price:
                exit_time = time
                exit_price = tp_price
                exit_reason = "TP"
                break
            
            # 檢查止損
            if direction == "LONG" and price <= sl_price:
                exit_time = time
                exit_price = sl_price
                exit_reason = "SL"
                break
            elif direction == "SHORT" and price >= sl_price:
                exit_time = time
                exit_price = sl_price
                exit_reason = "SL"
                break
            
            # 檢查時間停損
            if time >= max_exit_time:
                exit_time = time
                exit_price = price
                exit_reason = "TIME"
                break
        
        # 計算盈虧
        if direction == "LONG":
            pnl_pct = (exit_price - entry_price) / entry_price
        else:  # SHORT
            pnl_pct = (entry_price - exit_price) / entry_price
        
        pnl = pnl_pct * entry_price
        is_win = pnl_pct > 0
        
        return TradeResult(
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            direction=direction,
            holding_time_seconds=holding_time_seconds,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            pnl=pnl,
            pnl_pct=pnl_pct,
            is_win=is_win,
            exit_reason=exit_reason,
            vpin_at_entry=vpin,
            spread_at_entry=spread,
            volume_at_entry=volume
        )
    
    def run_experiment(
        self,
        signals: List[Dict],
        price_data: List[Tuple[datetime, float]],
        experiment_name: str = "sweep"
    ) -> Dict[Tuple[int, float, float], ExperimentResult]:
        """
        運行完整實驗
        
        Args:
            signals: 進場信號列表，每個信號包含:
                     {time, price, direction, vpin, spread, volume}
            price_data: 完整價格數據 [(時間, 價格), ...]
            experiment_name: 實驗名稱
            
        Returns:
            所有參數組合的實驗結果
        """
        logger.info(f"開始實驗: {experiment_name}")
        logger.info(f"信號數量: {len(signals)}")
        logger.info(f"價格數據點: {len(price_data)}")
        
        total_experiments = len(self.holding_times) * len(self.tp_levels) * len(self.sl_levels)
        current = 0
        
        # 遍歷所有參數組合
        for holding_time in self.holding_times:
            for tp_pct in self.tp_levels:
                for sl_pct in self.sl_levels:
                    current += 1
                    
                    # 創建實驗結果
                    result = ExperimentResult(
                        holding_time_seconds=holding_time,
                        take_profit_pct=tp_pct,
                        stop_loss_pct=sl_pct
                    )
                    
                    # 模擬所有交易
                    for signal in signals:
                        trade = self.simulate_trade(
                            entry_time=signal['time'],
                            entry_price=signal['price'],
                            direction=signal['direction'],
                            price_data=price_data,
                            holding_time_seconds=holding_time,
                            take_profit_pct=tp_pct,
                            stop_loss_pct=sl_pct,
                            vpin=signal.get('vpin'),
                            spread=signal.get('spread'),
                            volume=signal.get('volume')
                        )
                        
                        result.trades.append(trade)
                    
                    # 計算績效指標
                    self._calculate_metrics(result)
                    
                    # 存儲結果
                    key = (holding_time, tp_pct, sl_pct)
                    self.results[key] = result
                    
                    if current % 10 == 0:
                        logger.info(f"進度: {current}/{total_experiments} ({current/total_experiments*100:.1f}%)")
        
        logger.info(f"實驗完成！共 {total_experiments} 組參數")
        
        # 保存結果
        self.save_results(experiment_name)
        
        return self.results
    
    def _calculate_metrics(self, result: ExperimentResult):
        """計算績效指標"""
        if not result.trades:
            return
        
        result.total_trades = len(result.trades)
        result.winning_trades = sum(1 for t in result.trades if t.is_win)
        result.losing_trades = result.total_trades - result.winning_trades
        result.win_rate = result.winning_trades / result.total_trades if result.total_trades > 0 else 0
        
        # 盈虧統計
        wins = [t.pnl for t in result.trades if t.is_win]
        losses = [t.pnl for t in result.trades if not t.is_win]
        
        result.total_pnl = sum(t.pnl for t in result.trades)
        result.avg_win = np.mean(wins) if wins else 0
        result.avg_loss = np.mean(losses) if losses else 0
        
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0
        result.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')
        
        # 出場原因統計
        result.tp_exits = sum(1 for t in result.trades if t.exit_reason == "TP")
        result.sl_exits = sum(1 for t in result.trades if t.exit_reason == "SL")
        result.time_exits = sum(1 for t in result.trades if t.exit_reason == "TIME")
        result.forced_exits = sum(1 for t in result.trades if t.exit_reason == "FORCED")
        
        # 最大回撤
        cumulative_pnl = 0
        peak = 0
        max_dd = 0
        
        for trade in result.trades:
            cumulative_pnl += trade.pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd
        
        result.max_drawdown = max_dd
        
        # Sharpe Ratio
        returns = [t.pnl_pct for t in result.trades]
        if len(returns) > 1:
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            result.sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        
        # Sortino Ratio (只考慮下行風險)
        negative_returns = [r for r in returns if r < 0]
        if len(negative_returns) > 1:
            downside_std = np.std(negative_returns)
            result.sortino_ratio = (np.mean(returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0
    
    def get_best_parameters(
        self,
        metric: str = "sharpe_ratio",
        top_n: int = 10
    ) -> List[Tuple[Tuple[int, float, float], ExperimentResult]]:
        """
        獲取最佳參數組合
        
        Args:
            metric: 評估指標 (sharpe_ratio, profit_factor, win_rate 等)
            top_n: 返回前 N 名
            
        Returns:
            [(參數組合, 實驗結果), ...]
        """
        if not self.results:
            logger.warning("沒有實驗結果")
            return []
        
        # 排序
        sorted_results = sorted(
            self.results.items(),
            key=lambda x: getattr(x[1], metric, 0),
            reverse=True
        )
        
        return sorted_results[:top_n]
    
    def save_results(self, experiment_name: str):
        """保存實驗結果到 JSON"""
        output_file = self.output_dir / f"{experiment_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # 轉換為可序列化格式
        data = {
            'experiment_name': experiment_name,
            'timestamp': datetime.now().isoformat(),
            'total_experiments': len(self.results),
            'results': []
        }
        
        for (holding, tp, sl), result in self.results.items():
            data['results'].append({
                'parameters': {
                    'holding_time_seconds': holding,
                    'take_profit_pct': tp,
                    'stop_loss_pct': sl
                },
                'metrics': {
                    'total_trades': result.total_trades,
                    'win_rate': result.win_rate,
                    'profit_factor': result.profit_factor,
                    'sharpe_ratio': result.sharpe_ratio,
                    'sortino_ratio': result.sortino_ratio,
                    'max_drawdown': result.max_drawdown,
                    'total_pnl': result.total_pnl,
                    'avg_win': result.avg_win,
                    'avg_loss': result.avg_loss,
                    'tp_exits': result.tp_exits,
                    'sl_exits': result.sl_exits,
                    'time_exits': result.time_exits
                }
            })
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"結果已保存至: {output_file}")
    
    def generate_report(
        self,
        top_n: int = 10,
        output_file: Optional[str] = None
    ) -> str:
        """
        生成實驗報告
        
        Args:
            top_n: 顯示前 N 名
            output_file: 輸出檔案路徑（可選）
            
        Returns:
            報告文本
        """
        if not self.results:
            return "沒有實驗結果"
        
        report = []
        report.append("=" * 80)
        report.append("持倉時間實驗報告")
        report.append("=" * 80)
        report.append(f"實驗時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"總實驗數: {len(self.results)}")
        report.append("")
        
        # 按不同指標排序的前 N 名
        metrics = [
            ('sharpe_ratio', 'Sharpe Ratio'),
            ('profit_factor', 'Profit Factor'),
            ('win_rate', 'Win Rate'),
            ('sortino_ratio', 'Sortino Ratio')
        ]
        
        for metric_key, metric_name in metrics:
            report.append(f"\n{'=' * 80}")
            report.append(f"Top {top_n} by {metric_name}")
            report.append("=" * 80)
            report.append(f"{'Rank':<5} {'Hold Time':<12} {'TP%':<8} {'SL%':<8} {metric_name:<15} {'Win Rate':<10} {'Trades':<8}")
            report.append("-" * 80)
            
            best = self.get_best_parameters(metric=metric_key, top_n=top_n)
            
            for rank, ((holding, tp, sl), result) in enumerate(best, 1):
                holding_str = self._format_holding_time(holding)
                report.append(
                    f"{rank:<5} {holding_str:<12} {tp*100:<7.2f}% {sl*100:<7.2f}% "
                    f"{getattr(result, metric_key):<15.3f} {result.win_rate*100:<9.1f}% {result.total_trades:<8}"
                )
        
        # 綜合最佳建議
        report.append(f"\n{'=' * 80}")
        report.append("綜合最佳建議")
        report.append("=" * 80)
        
        best_sharpe = self.get_best_parameters(metric='sharpe_ratio', top_n=1)[0]
        (holding, tp, sl), result = best_sharpe
        
        report.append(f"最佳持倉時間: {self._format_holding_time(holding)}")
        report.append(f"最佳止盈: {tp*100:.2f}%")
        report.append(f"最佳止損: {sl*100:.2f}%")
        report.append(f"預期勝率: {result.win_rate*100:.1f}%")
        report.append(f"Profit Factor: {result.profit_factor:.2f}")
        report.append(f"Sharpe Ratio: {result.sharpe_ratio:.3f}")
        report.append(f"Max Drawdown: ${result.max_drawdown:.2f}")
        
        report_text = "\n".join(report)
        
        # 保存到檔案
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report_text)
            logger.info(f"報告已保存至: {output_file}")
        
        return report_text
    
    def _format_holding_time(self, seconds: int) -> str:
        """格式化持倉時間"""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds//60}m"
        else:
            return f"{seconds//3600}h"


# ==================== 使用範例 ====================
if __name__ == "__main__":
    # 配置日誌
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 創建實驗台
    sweeper = HoldingTimeSweeper(
        holding_times=[30, 60, 180, 300, 600],  # 30s, 1m, 3m, 5m, 10m
        tp_levels=[0.003, 0.005, 0.008],        # 0.3%, 0.5%, 0.8%
        sl_levels=[0.001, 0.002, 0.003]         # 0.1%, 0.2%, 0.3%
    )
    
    # 模擬價格數據（示例）
    base_time = datetime.now()
    price_data = []
    base_price = 100000
    
    for i in range(1000):
        time = base_time + timedelta(seconds=i * 10)
        # 隨機遊走
        price = base_price + np.random.randn() * 100
        price_data.append((time, price))
    
    # 模擬進場信號（示例）
    signals = [
        {
            'time': base_time + timedelta(seconds=i * 100),
            'price': 100000 + np.random.randn() * 50,
            'direction': 'LONG' if np.random.rand() > 0.5 else 'SHORT',
            'vpin': np.random.rand() * 0.5,
            'spread': np.random.rand() * 0.001,
            'volume': np.random.rand() * 1000
        }
        for i in range(50)
    ]
    
    # 運行實驗
    results = sweeper.run_experiment(signals, price_data, "test_sweep")
    
    # 生成報告
    report = sweeper.generate_report(top_n=5)
    print(report)
