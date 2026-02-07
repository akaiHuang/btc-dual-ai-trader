#!/usr/bin/env python3
"""
🎴 卡片回測系統 - 分析不同策略卡片在歷史數據上的表現
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

# 添加專案根目錄到路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

@dataclass
class Trade:
    """單筆交易"""
    timestamp: str
    direction: str  # LONG / SHORT
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usdt: float
    hold_seconds: float
    mode: str  # M0, M1, M2, etc.
    six_dim_score: int = 0
    obi: float = 0.0
    regime: str = ""

@dataclass
class CardConfig:
    """卡片配置"""
    card_id: str
    name: str
    # 進場條件
    min_six_dim_score: int = 6
    min_probability: float = 0.50
    min_confidence: float = 0.25
    # 出場條件  
    target_profit_pct: float = 0.40
    stop_loss_pct: float = 0.20
    max_hold_minutes: float = 30.0
    # OBI 過濾
    obi_long_threshold: float = 0.10
    obi_short_threshold: float = -0.10
    # N% 鎖 N%
    use_n_lock_n: bool = True
    n_lock_n_threshold: float = 1.0

def load_trades_from_logs(log_dir: str, hours: int = 24) -> List[Trade]:
    """從日誌載入交易記錄"""
    trades = []
    cutoff = datetime.now() - timedelta(hours=hours)
    
    log_path = Path(log_dir)
    for trade_file in sorted(log_path.glob("trades_*.json"), reverse=True):
        try:
            # 解析檔名時間
            ts_str = trade_file.stem.replace("trades_", "")
            # 嘗試不同格式
            try:
                file_time = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            except ValueError:
                try:
                    file_time = datetime.strptime(ts_str, "%Y%m%d")
                except ValueError:
                    continue
            
            if file_time < cutoff:
                continue
            
            with open(trade_file) as f:
                data = json.load(f)
            
            for t in data.get("trades", []):
                # 提取六維分數 (從 strategy_probs 推估)
                strategy_probs = t.get("strategy_probs", {})
                probability = t.get("probability", 0)
                confidence = t.get("confidence", 0)
                
                trade = Trade(
                    timestamp=t.get("entry_time", t.get("timestamp", "")),
                    direction=t.get("direction", "UNKNOWN"),
                    entry_price=float(t.get("entry_price", 0)),
                    exit_price=float(t.get("exit_price", t.get("entry_price", 0))),
                    pnl_pct=float(t.get("pnl_pct", 0)),
                    pnl_usdt=float(t.get("pnl_usdt", t.get("net_pnl_usdt", 0))),
                    hold_seconds=float(t.get("hold_seconds", 0)),
                    mode=t.get("strategy", "UNKNOWN"),
                    six_dim_score=int(t.get("six_dim_score", 0)),  # 可能為0
                    obi=float(t.get("obi", 0)),
                    regime=t.get("regime", t.get("market_regime", "")),
                )
                # 額外屬性
                trade.probability = probability
                trade.confidence = confidence
                trade.leverage = t.get("leverage", 50)
                trade.max_profit_pct = t.get("max_profit_pct", 0)
                trade.max_drawdown_pct = t.get("max_drawdown_pct", 0)
                trade.actual_target_pct = t.get("actual_target_pct", 0)
                trade.actual_stop_loss_pct = t.get("actual_stop_loss_pct", 0)
                
                trades.append(trade)
        except Exception as e:
            print(f"⚠️ 讀取 {trade_file} 失敗: {e}")
    
    return trades

def load_signals_from_logs(log_dir: str, hours: int = 24) -> List[Dict]:
    """從日誌載入信號記錄 (用於模擬回測)"""
    signals = []
    cutoff = datetime.now() - timedelta(hours=hours)
    
    log_path = Path(log_dir)
    for signal_file in sorted(log_path.glob("signals_*.json"), reverse=True):
        try:
            ts_str = signal_file.stem.replace("signals_", "")
            file_time = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            
            if file_time < cutoff:
                continue
            
            with open(signal_file) as f:
                data = json.load(f)
            
            for s in data.get("signals", []):
                signals.append(s)
        except Exception as e:
            pass
    
    return signals

def get_card_configs() -> List[CardConfig]:
    """載入所有卡片配置"""
    cards = []
    cards_dir = Path(__file__).parent.parent / "config" / "trading_cards"
    
    for card_file in cards_dir.glob("*.json"):
        if card_file.name == "master_config.json":
            continue
        
        try:
            with open(card_file) as f:
                data = json.load(f)
            
            meta = data.get("_meta", {})
            entry = data.get("entry", {})
            exit_cfg = data.get("exit", {})
            risk = data.get("risk", {})
            
            # 兼容平鋪格式
            card = CardConfig(
                card_id=meta.get("card_id", card_file.stem),
                name=meta.get("card_name", card_file.stem),
                min_six_dim_score=entry.get("six_dim_min_score_to_trade", 
                                           data.get("six_dim_min_score_to_trade", 6)),
                min_probability=entry.get("min_probability", 
                                         data.get("min_probability", 0.50)),
                min_confidence=entry.get("min_confidence",
                                        data.get("min_confidence", 0.25)),
                target_profit_pct=exit_cfg.get("target_profit_pct",
                                              data.get("target_profit_pct", 0.40)),
                stop_loss_pct=exit_cfg.get("stop_loss_pct",
                                          data.get("stop_loss_pct", 0.20)),
                max_hold_minutes=exit_cfg.get("max_hold_minutes",
                                             data.get("max_hold_minutes", 30.0)),
                obi_long_threshold=entry.get("obi_long_threshold",
                                            data.get("obi_long_threshold", 0.10)),
                obi_short_threshold=entry.get("obi_short_threshold",
                                             data.get("obi_short_threshold", -0.10)),
                use_n_lock_n=exit_cfg.get("use_n_lock_n",
                                         data.get("use_n_lock_n", True)),
                n_lock_n_threshold=exit_cfg.get("n_lock_n_threshold",
                                               data.get("n_lock_n_threshold", 1.0)),
            )
            cards.append(card)
        except Exception as e:
            print(f"⚠️ 載入卡片 {card_file} 失敗: {e}")
    
    return cards

def simulate_with_card(signals: List[Dict], card: CardConfig) -> Dict:
    """使用卡片配置模擬交易"""
    results = {
        "card_id": card.card_id,
        "card_name": card.name,
        "total_signals": 0,
        "filtered_signals": 0,
        "would_enter": 0,
        "simulated_trades": [],
    }
    
    for sig in signals:
        results["total_signals"] += 1
        
        six_dim = sig.get("six_dim", {})
        long_score = six_dim.get("long_score", 0)
        short_score = six_dim.get("short_score", 0)
        direction = sig.get("direction", "NONE")
        market = sig.get("market", {})
        obi = market.get("obi", 0)
        
        # 判斷是否符合進場條件
        score = long_score if direction == "LONG" else short_score
        
        # 六維分數過濾
        if score < card.min_six_dim_score:
            continue
        
        # OBI 過濾
        if direction == "LONG" and obi < card.obi_long_threshold:
            continue
        if direction == "SHORT" and obi > card.obi_short_threshold:
            continue
        
        results["filtered_signals"] += 1
        
        # 判斷是否會進場 (LONG_READY / SHORT_READY)
        if sig.get("signal_type", "").endswith("_READY"):
            results["would_enter"] += 1
            results["simulated_trades"].append({
                "timestamp": sig.get("timestamp"),
                "direction": direction,
                "score": score,
                "obi": obi,
                "price": sig.get("price", 0)
            })
    
    return results

def backtest_with_actual_trades(trades: List[Trade], card: CardConfig) -> Dict:
    """使用實際交易數據回測卡片"""
    matching_trades = []
    
    for trade in trades:
        # 檢查六維分數是否符合
        if trade.six_dim_score < card.min_six_dim_score:
            continue
        
        # 檢查 OBI 條件
        if trade.direction == "LONG" and trade.obi < card.obi_long_threshold:
            continue
        if trade.direction == "SHORT" and trade.obi > card.obi_short_threshold:
            continue
        
        matching_trades.append(trade)
    
    if not matching_trades:
        return {
            "card_id": card.card_id,
            "card_name": card.name,
            "total_trades": 0,
            "win_rate": 0,
            "total_pnl_pct": 0,
            "total_pnl_usdt": 0,
            "avg_pnl_pct": 0,
            "avg_win_pct": 0,
            "avg_loss_pct": 0,
            "profit_factor": 0,
            "matching_trades": []
        }
    
    wins = [t for t in matching_trades if t.pnl_pct > 0]
    losses = [t for t in matching_trades if t.pnl_pct <= 0]
    
    total_pnl_pct = sum(t.pnl_pct for t in matching_trades)
    total_pnl_usdt = sum(t.pnl_usdt for t in matching_trades)
    
    gross_profit = sum(t.pnl_pct for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 0.001
    
    return {
        "card_id": card.card_id,
        "card_name": card.name,
        "total_trades": len(matching_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(matching_trades) * 100 if matching_trades else 0,
        "total_pnl_pct": total_pnl_pct,
        "total_pnl_usdt": total_pnl_usdt,
        "avg_pnl_pct": total_pnl_pct / len(matching_trades) if matching_trades else 0,
        "avg_win_pct": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
        "avg_loss_pct": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0,
        "matching_trades": matching_trades
    }

def analyze_by_mode(trades: List[Trade]) -> Dict[str, Dict]:
    """按模式/策略分析交易"""
    by_mode = defaultdict(list)
    for t in trades:
        by_mode[t.mode].append(t)
    
    results = {}
    for mode, mode_trades in by_mode.items():
        wins = [t for t in mode_trades if t.pnl_pct > 0]
        results[mode] = {
            "total": len(mode_trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(mode_trades) * 100 if mode_trades else 0,
            "total_pnl_pct": sum(t.pnl_pct for t in mode_trades),
            "avg_pnl_pct": sum(t.pnl_pct for t in mode_trades) / len(mode_trades) if mode_trades else 0,
            "total_pnl_usdt": sum(t.pnl_usdt for t in mode_trades),
        }
    
    return results

def analyze_by_direction(trades: List[Trade]) -> Dict[str, Dict]:
    """按方向分析交易"""
    by_dir = defaultdict(list)
    for t in trades:
        by_dir[t.direction].append(t)
    
    results = {}
    for direction, dir_trades in by_dir.items():
        wins = [t for t in dir_trades if t.pnl_pct > 0]
        results[direction] = {
            "total": len(dir_trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(dir_trades) * 100 if dir_trades else 0,
            "total_pnl_pct": sum(t.pnl_pct for t in dir_trades),
            "avg_pnl_pct": sum(t.pnl_pct for t in dir_trades) / len(dir_trades) if dir_trades else 0,
        }
    
    return results

def analyze_by_leverage(trades: List[Trade]) -> Dict[str, Dict]:
    """按槓桿分析交易"""
    by_lev = defaultdict(list)
    for t in trades:
        lev = getattr(t, 'leverage', 50)
        # 分組: 1-10x, 11-20x, 21-30x, 31-40x, 41-50x
        if lev <= 10:
            group = "1-10x"
        elif lev <= 20:
            group = "11-20x"
        elif lev <= 30:
            group = "21-30x"
        elif lev <= 40:
            group = "31-40x"
        else:
            group = "41-50x"
        by_lev[group].append(t)
    
    results = {}
    for group, group_trades in sorted(by_lev.items()):
        wins = [t for t in group_trades if t.pnl_pct > 0]
        results[group] = {
            "total": len(group_trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(group_trades) * 100 if group_trades else 0,
            "total_pnl_pct": sum(t.pnl_pct for t in group_trades),
            "avg_pnl_pct": sum(t.pnl_pct for t in group_trades) / len(group_trades) if group_trades else 0,
        }
    
    return results

def analyze_by_obi(trades: List[Trade]) -> Dict[str, Dict]:
    """按 OBI 分析交易"""
    by_obi = defaultdict(list)
    for t in trades:
        obi = t.obi
        # 分組
        if obi < -0.3:
            group = "< -0.3 (強賣壓)"
        elif obi < -0.1:
            group = "-0.3 ~ -0.1 (賣壓)"
        elif obi < 0.1:
            group = "-0.1 ~ 0.1 (中性)"
        elif obi < 0.3:
            group = "0.1 ~ 0.3 (買壓)"
        else:
            group = "> 0.3 (強買壓)"
        by_obi[group].append(t)
    
    results = {}
    for group in ["< -0.3 (強賣壓)", "-0.3 ~ -0.1 (賣壓)", "-0.1 ~ 0.1 (中性)", "0.1 ~ 0.3 (買壓)", "> 0.3 (強買壓)"]:
        group_trades = by_obi.get(group, [])
        if group_trades:
            wins = [t for t in group_trades if t.pnl_pct > 0]
            results[group] = {
                "total": len(group_trades),
                "wins": len(wins),
                "win_rate": len(wins) / len(group_trades) * 100,
                "total_pnl_pct": sum(t.pnl_pct for t in group_trades),
                "avg_pnl_pct": sum(t.pnl_pct for t in group_trades) / len(group_trades),
            }
    
    return results

def analyze_by_score(trades: List[Trade]) -> Dict[int, Dict]:
    """按六維分數分析交易"""
    by_score = defaultdict(list)
    for t in trades:
        by_score[t.six_dim_score].append(t)
    
    results = {}
    for score, score_trades in sorted(by_score.items()):
        wins = [t for t in score_trades if t.pnl_pct > 0]
        results[score] = {
            "total": len(score_trades),
            "wins": len(wins),
            "win_rate": len(wins) / len(score_trades) * 100 if score_trades else 0,
            "total_pnl_pct": sum(t.pnl_pct for t in score_trades),
            "avg_pnl_pct": sum(t.pnl_pct for t in score_trades) / len(score_trades) if score_trades else 0
        }
    
    return results

def main():
    print("=" * 70)
    print("🎴 卡片回測系統 - 24 小時數據分析")
    print("=" * 70)
    
    # 1. 載入交易數據
    log_dir = Path(__file__).parent.parent / "logs" / "whale_paper_trader"
    trades = load_trades_from_logs(str(log_dir), hours=48)
    signals = load_signals_from_logs(str(log_dir), hours=48)
    
    print(f"\n📊 數據概覽:")
    print(f"   交易記錄: {len(trades)} 筆")
    print(f"   信號記錄: {len(signals)} 筆")
    
    if not trades:
        print("\n❌ 沒有找到交易數據！")
        return
    
    # 2. 整體統計
    print("\n" + "=" * 70)
    print("📈 整體交易統計 (48h)")
    print("=" * 70)
    
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    total_pnl = sum(t.pnl_pct for t in trades)
    
    print(f"   總交易數: {len(trades)}")
    print(f"   勝: {len(wins)} / 負: {len(losses)}")
    print(f"   勝率: {len(wins)/len(trades)*100:.1f}%")
    print(f"   總 PnL: {total_pnl:+.2f}%")
    print(f"   平均 PnL: {total_pnl/len(trades):+.3f}%")
    
    if wins:
        print(f"   平均獲利: +{sum(t.pnl_pct for t in wins)/len(wins):.3f}%")
    if losses:
        print(f"   平均虧損: {sum(t.pnl_pct for t in losses)/len(losses):.3f}%")
    
    # 3. 按模式分析
    print("\n" + "=" * 70)
    print("📊 按策略/模式分析")
    print("=" * 70)
    
    mode_results = analyze_by_mode(trades)
    sorted_modes = sorted(mode_results.items(), key=lambda x: x[1]["win_rate"], reverse=True)
    
    print(f"\n{'Strategy':<20} {'Trades':>8} {'Win Rate':>10} {'PnL %':>10} {'Avg PnL':>10} {'PnL $':>10}")
    print("-" * 70)
    for mode, stats in sorted_modes:
        print(f"{mode:<20} {stats['total']:>8} {stats['win_rate']:>9.1f}% {stats['total_pnl_pct']:>+9.2f}% {stats['avg_pnl_pct']:>+9.3f}% ${stats['total_pnl_usdt']:>+8.1f}")
    
    # 3.5 按方向分析
    print("\n" + "=" * 70)
    print("📊 按方向分析 (LONG vs SHORT)")
    print("=" * 70)
    
    dir_results = analyze_by_direction(trades)
    print(f"\n{'Direction':<12} {'Trades':>8} {'Win Rate':>10} {'PnL %':>12} {'Avg PnL':>10}")
    print("-" * 55)
    for direction, stats in dir_results.items():
        print(f"{direction:<12} {stats['total']:>8} {stats['win_rate']:>9.1f}% {stats['total_pnl_pct']:>+11.2f}% {stats['avg_pnl_pct']:>+9.3f}%")
    
    # 3.6 按槓桿分析
    print("\n" + "=" * 70)
    print("📊 按槓桿分析")
    print("=" * 70)
    
    lev_results = analyze_by_leverage(trades)
    print(f"\n{'Leverage':<12} {'Trades':>8} {'Win Rate':>10} {'PnL %':>12} {'Avg PnL':>10}")
    print("-" * 55)
    for group, stats in lev_results.items():
        print(f"{group:<12} {stats['total']:>8} {stats['win_rate']:>9.1f}% {stats['total_pnl_pct']:>+11.2f}% {stats['avg_pnl_pct']:>+9.3f}%")
    
    # 3.7 按 OBI 分析
    print("\n" + "=" * 70)
    print("📊 按 OBI (訂單簿失衡) 分析")
    print("=" * 70)
    
    obi_results = analyze_by_obi(trades)
    print(f"\n{'OBI Range':<22} {'Trades':>8} {'Win Rate':>10} {'PnL %':>12} {'Avg PnL':>10}")
    print("-" * 65)
    for group, stats in obi_results.items():
        print(f"{group:<22} {stats['total']:>8} {stats['win_rate']:>9.1f}% {stats['total_pnl_pct']:>+11.2f}% {stats['avg_pnl_pct']:>+9.3f}%")
    
    # 4. 按六維分數分析
    print("\n" + "=" * 70)
    print("📊 按六維分數分析")
    print("=" * 70)
    
    score_results = analyze_by_score(trades)
    
    print(f"\n{'Score':>6} {'Trades':>8} {'Win Rate':>10} {'PnL %':>10} {'Avg PnL':>10}")
    print("-" * 50)
    for score, stats in sorted(score_results.items()):
        print(f"{score:>6} {stats['total']:>8} {stats['win_rate']:>9.1f}% {stats['total_pnl_pct']:>+9.2f}% {stats['avg_pnl_pct']:>+9.3f}%")
    
    # 5. 卡片回測
    print("\n" + "=" * 70)
    print("🎴 卡片策略回測")
    print("=" * 70)
    
    cards = get_card_configs()
    card_results = []
    
    for card in cards:
        result = backtest_with_actual_trades(trades, card)
        card_results.append(result)
    
    # 按勝率排序
    sorted_by_winrate = sorted(card_results, key=lambda x: x["win_rate"], reverse=True)
    
    print(f"\n{'Card':<25} {'Trades':>7} {'Wins':>6} {'WinRate':>8} {'PnL%':>9} {'AvgPnL':>8} {'PF':>6}")
    print("-" * 75)
    for r in sorted_by_winrate:
        if r["total_trades"] > 0:
            print(f"{r['card_name'][:24]:<25} {r['total_trades']:>7} {r['wins']:>6} {r['win_rate']:>7.1f}% {r['total_pnl_pct']:>+8.2f}% {r['avg_pnl_pct']:>+7.3f}% {r['profit_factor']:>5.2f}")
    
    # 6. 最佳卡片建議
    print("\n" + "=" * 70)
    print("🏆 最佳卡片建議")
    print("=" * 70)
    
    # 過濾有足夠交易數的卡片
    valid_cards = [r for r in card_results if r["total_trades"] >= 5]
    
    if valid_cards:
        best_winrate = max(valid_cards, key=lambda x: x["win_rate"])
        best_pnl = max(valid_cards, key=lambda x: x["total_pnl_pct"])
        best_pf = max(valid_cards, key=lambda x: x["profit_factor"])
        
        print(f"\n🎯 最高勝率: {best_winrate['card_name']}")
        print(f"   勝率: {best_winrate['win_rate']:.1f}% ({best_winrate['wins']}/{best_winrate['total_trades']})")
        print(f"   總 PnL: {best_winrate['total_pnl_pct']:+.2f}%")
        
        print(f"\n💰 最高獲利: {best_pnl['card_name']}")
        print(f"   總 PnL: {best_pnl['total_pnl_pct']:+.2f}%")
        print(f"   勝率: {best_pnl['win_rate']:.1f}%")
        
        print(f"\n📊 最佳盈虧比: {best_pf['card_name']}")
        print(f"   Profit Factor: {best_pf['profit_factor']:.2f}")
        print(f"   勝率: {best_pf['win_rate']:.1f}%")
    
    # 7. 找出最佳六維分數門檻
    print("\n" + "=" * 70)
    print("🔍 最佳六維分數門檻分析")
    print("=" * 70)
    
    for min_score in range(4, 11):
        filtered = [t for t in trades if t.six_dim_score >= min_score]
        if filtered:
            wins_f = [t for t in filtered if t.pnl_pct > 0]
            wr = len(wins_f) / len(filtered) * 100
            pnl = sum(t.pnl_pct for t in filtered)
            print(f"   Score ≥ {min_score}: {len(filtered):>4} trades, WR: {wr:>5.1f}%, PnL: {pnl:>+7.2f}%")
    
    # 保存結果
    output = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_trades": len(trades),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0,
            "total_pnl_pct": total_pnl
        },
        "by_mode": {k: v for k, v in mode_results.items()},
        "by_score": {str(k): v for k, v in score_results.items()},
        "card_results": [{k: v for k, v in r.items() if k != "matching_trades"} for r in card_results]
    }
    
    output_path = log_dir / "backtest_cards_result.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n📁 結果已保存: {output_path}")

if __name__ == "__main__":
    main()
