#!/usr/bin/env python3
"""
自動校正機制 - 定期驗證信號準確率並自動調整參數

功能:
1. 每 N 小時自動運行驗證
2. 根據驗證結果調整門檻參數
3. 保存調整記錄到 calibration 配置
4. 支援手動觸發和自動排程
"""
import json
import sys
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import ccxt
except ImportError:
    print("需要安裝 ccxt: pip install ccxt")
    sys.exit(1)


class AutoCalibrator:
    """自動校正器"""
    
    def __init__(self, config_path="config/calibration/signal_calibration.json"):
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.calibration = self._load_config()
        self.exchange = ccxt.binance({"timeout": 15000})
        
    def _load_config(self):
        """載入校正配置"""
        if self.config_path.exists():
            with open(self.config_path) as f:
                return json.load(f)
        return {
            "last_update": None,
            "validation_summary": {},
            "six_dim": {"enabled": True, "optimal_threshold": 8},
            "obi": {"enabled": True, "optimal_long_threshold": 0.08, "optimal_short_threshold": -0.05},
            "auto_calibration": {
                "enabled": True,
                "run_interval_hours": 24,
                "min_sample_size": 100,
                "auto_adjust_thresholds": False,
                "history": []
            }
        }
    
    def _save_config(self):
        """保存校正配置"""
        with open(self.config_path, "w") as f:
            json.dump(self.calibration, f, indent=2, ensure_ascii=False)
    
    def load_signals(self, hours=24):
        """載入信號記錄"""
        signals = []
        log_dir = Path("logs/whale_paper_trader")
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        for f in sorted(log_dir.glob("signals_*.json"), reverse=True)[:50]:
            try:
                with open(f) as fp:
                    data = json.load(fp)
                    for s in data.get("signals", []):
                        if s.get("signal_type") == "ENTERED":
                            sig_time = datetime.fromisoformat(s["timestamp"].replace("Z", ""))
                            if sig_time >= cutoff_time:
                                signals.append(s)
            except Exception:
                pass
        
        return signals
    
    def validate_signals(self, signals, lookahead_minutes=5):
        """驗證信號準確率"""
        if not signals:
            return None
        
        # 按時間排序
        signals.sort(key=lambda x: x["timestamp"])
        signals = signals[:500]
        
        # 獲取時間範圍
        first_time = datetime.fromisoformat(signals[0]["timestamp"].replace("Z", ""))
        since = int((first_time - timedelta(minutes=10)).timestamp() * 1000)
        
        try:
            ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "1m", since=since, limit=1000)
        except Exception as e:
            print(f"獲取 K 線失敗: {e}")
            return None
        
        # 建立時間索引
        kline_dict = {}
        for k in ohlcv:
            ts = datetime.fromtimestamp(k[0] / 1000)
            kline_dict[ts.strftime("%Y-%m-%d %H:%M")] = {
                "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]
            }
        
        # 驗證結果
        results = {
            "total": 0,
            "correct": 0,
            "by_score": defaultdict(lambda: {"total": 0, "correct": 0}),
            "by_direction": {"LONG": {"total": 0, "correct": 0}, "SHORT": {"total": 0, "correct": 0}},
            "by_obi_range": {
                "strong_long": {"total": 0, "correct": 0},
                "mild_long": {"total": 0, "correct": 0},
                "neutral": {"total": 0, "correct": 0},
                "mild_short": {"total": 0, "correct": 0},
                "strong_short": {"total": 0, "correct": 0}
            }
        }
        
        for sig in signals:
            try:
                sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
                sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
                future_minute = (sig_time + timedelta(minutes=lookahead_minutes)).strftime("%Y-%m-%d %H:%M")
                
                entry_kline = kline_dict.get(sig_minute)
                future_kline = kline_dict.get(future_minute)
                
                if not entry_kline or not future_kline:
                    continue
                
                direction = sig.get("direction", "")
                six_dim = sig.get("six_dim", {})
                market = sig.get("market", {})
                obi = market.get("obi", 0)
                
                score = max(six_dim.get("long_score", 0), six_dim.get("short_score", 0))
                
                entry_price = entry_kline["close"]
                future_price = future_kline["close"]
                change_pct = (future_price - entry_price) / entry_price * 100
                
                is_correct = (direction == "LONG" and change_pct > 0) or \
                            (direction == "SHORT" and change_pct < 0)
                
                results["total"] += 1
                results["by_score"][score]["total"] += 1
                
                if direction in results["by_direction"]:
                    results["by_direction"][direction]["total"] += 1
                
                # OBI 區間分類
                if obi > 0.1:
                    obi_range = "strong_long"
                elif obi > 0.05:
                    obi_range = "mild_long"
                elif obi > -0.05:
                    obi_range = "neutral"
                elif obi > -0.1:
                    obi_range = "mild_short"
                else:
                    obi_range = "strong_short"
                results["by_obi_range"][obi_range]["total"] += 1
                
                if is_correct:
                    results["correct"] += 1
                    results["by_score"][score]["correct"] += 1
                    if direction in results["by_direction"]:
                        results["by_direction"][direction]["correct"] += 1
                    results["by_obi_range"][obi_range]["correct"] += 1
                    
            except Exception:
                pass
        
        return results
    
    def calculate_optimal_thresholds(self, results):
        """計算最佳門檻"""
        if not results or results["total"] < 50:
            return None
        
        recommendations = {
            "six_dim": {},
            "obi": {},
            "direction": {}
        }
        
        # 1. 找最佳六維分數門檻
        best_score = None
        best_accuracy = 0
        
        for score in sorted(results["by_score"].keys(), reverse=True):
            data = results["by_score"][score]
            if data["total"] >= 10:
                acc = data["correct"] / data["total"]
                if acc > best_accuracy:
                    best_accuracy = acc
                    best_score = score
        
        if best_score:
            recommendations["six_dim"]["optimal_threshold"] = best_score
            recommendations["six_dim"]["optimal_accuracy"] = round(best_accuracy * 100, 1)
        
        # 2. 方向專用門檻
        for direction in ["LONG", "SHORT"]:
            data = results["by_direction"].get(direction, {})
            if data.get("total", 0) >= 20:
                acc = data["correct"] / data["total"]
                recommendations["direction"][direction] = {
                    "accuracy": round(acc * 100, 1),
                    "sample_size": data["total"]
                }
        
        # 3. OBI 門檻建議
        for obi_range, data in results["by_obi_range"].items():
            if data["total"] >= 10:
                acc = data["correct"] / data["total"]
                recommendations["obi"][obi_range] = {
                    "accuracy": round(acc * 100, 1),
                    "sample_size": data["total"]
                }
        
        return recommendations
    
    def auto_adjust(self, recommendations, force=False):
        """自動調整參數"""
        auto_config = self.calibration.get("auto_calibration", {})
        
        if not auto_config.get("auto_adjust_thresholds", False) and not force:
            print("⚠️ 自動調整已禁用 (auto_adjust_thresholds: false)")
            return False
        
        adjustments = []
        
        # 調整六維門檻
        if "six_dim" in recommendations:
            new_threshold = recommendations["six_dim"].get("optimal_threshold")
            if new_threshold:
                old_threshold = self.calibration.get("six_dim", {}).get("optimal_threshold", 8)
                if new_threshold != old_threshold:
                    self.calibration.setdefault("six_dim", {})["optimal_threshold"] = new_threshold
                    adjustments.append(f"六維門檻: {old_threshold} → {new_threshold}")
        
        # 調整方向門檻
        if "direction" in recommendations:
            long_data = recommendations["direction"].get("LONG", {})
            short_data = recommendations["direction"].get("SHORT", {})
            
            # LONG 準確率低於 50% → 提高門檻
            if long_data.get("accuracy", 50) < 50:
                current = self.calibration.get("six_dim", {}).get("six_dim_min_score_long", 10)
                new_val = min(current + 1, 12)
                if new_val != current:
                    self.calibration.setdefault("six_dim", {})["six_dim_min_score_long"] = new_val
                    adjustments.append(f"LONG門檻: {current} → {new_val}")
            
            # SHORT 準確率高於 60% → 可降低門檻
            if short_data.get("accuracy", 50) > 60:
                current = self.calibration.get("six_dim", {}).get("six_dim_min_score_short", 8)
                new_val = max(current - 1, 6)
                if new_val != current:
                    self.calibration.setdefault("six_dim", {})["six_dim_min_score_short"] = new_val
                    adjustments.append(f"SHORT門檻: {current} → {new_val}")
        
        if adjustments:
            # 記錄調整歷史
            history = self.calibration.setdefault("auto_calibration", {}).setdefault("history", [])
            history.append({
                "timestamp": datetime.now().isoformat(),
                "adjustments": adjustments
            })
            # 只保留最近 20 條記錄
            self.calibration["auto_calibration"]["history"] = history[-20:]
            
            print(f"✅ 已自動調整 {len(adjustments)} 項參數:")
            for adj in adjustments:
                print(f"   - {adj}")
            
            return True
        else:
            print("ℹ️ 無需調整參數")
            return False
    
    def run(self, hours=24, auto_adjust=False):
        """執行校正"""
        print("=" * 60)
        print("🔄 自動校正系統")
        print("=" * 60)
        
        # 載入信號
        print(f"\n📥 載入最近 {hours} 小時信號...")
        signals = self.load_signals(hours=hours)
        print(f"共載入 {len(signals)} 筆進場信號")
        
        if len(signals) < 50:
            print("⚠️ 信號數量不足 (最少需要 50 筆)")
            return
        
        # 驗證信號
        print("\n🔍 驗證信號準確率...")
        results = self.validate_signals(signals)
        
        if not results or results["total"] == 0:
            print("❌ 驗證失敗")
            return
        
        # 輸出結果
        accuracy = results["correct"] / results["total"] * 100
        print(f"\n📊 驗證結果:")
        print(f"   總信號: {results['total']}")
        print(f"   準確率: {accuracy:.1f}%")
        
        print("\n📈 按方向:")
        for direction, data in results["by_direction"].items():
            if data["total"] > 0:
                acc = data["correct"] / data["total"] * 100
                print(f"   {direction}: {data['correct']}/{data['total']} ({acc:.1f}%)")
        
        print("\n📈 按分數:")
        for score in sorted(results["by_score"].keys(), reverse=True):
            data = results["by_score"][score]
            if data["total"] >= 5:
                acc = data["correct"] / data["total"] * 100
                print(f"   {score}分: {data['correct']}/{data['total']} ({acc:.1f}%)")
        
        # 計算建議
        print("\n💡 計算最佳參數...")
        recommendations = self.calculate_optimal_thresholds(results)
        
        if recommendations:
            print("\n📌 建議:")
            if "six_dim" in recommendations:
                opt = recommendations["six_dim"].get("optimal_threshold")
                acc = recommendations["six_dim"].get("optimal_accuracy")
                print(f"   六維最佳門檻: {opt} (準確率 {acc}%)")
            
            if "direction" in recommendations:
                for dir_name, data in recommendations["direction"].items():
                    print(f"   {dir_name} 準確率: {data['accuracy']}%")
        
        # 更新配置
        self.calibration["last_update"] = datetime.now().isoformat()
        self.calibration["validation_summary"] = {
            "total_signals": results["total"],
            "overall_accuracy": round(accuracy, 1),
            "long_accuracy": round(results["by_direction"]["LONG"]["correct"] / 
                                   results["by_direction"]["LONG"]["total"] * 100, 1) 
                            if results["by_direction"]["LONG"]["total"] > 0 else 0,
            "short_accuracy": round(results["by_direction"]["SHORT"]["correct"] / 
                                    results["by_direction"]["SHORT"]["total"] * 100, 1)
                            if results["by_direction"]["SHORT"]["total"] > 0 else 0
        }
        
        # 自動調整
        if auto_adjust:
            self.auto_adjust(recommendations, force=True)
        
        # 保存
        self._save_config()
        print(f"\n💾 已保存: {self.config_path}")
        
        return results, recommendations


def main():
    parser = argparse.ArgumentParser(description="自動校正系統")
    parser.add_argument("--hours", type=int, default=24, help="分析最近 N 小時的信號")
    parser.add_argument("--auto-adjust", action="store_true", help="自動調整參數")
    args = parser.parse_args()
    
    calibrator = AutoCalibrator()
    calibrator.run(hours=args.hours, auto_adjust=args.auto_adjust)


if __name__ == "__main__":
    main()
