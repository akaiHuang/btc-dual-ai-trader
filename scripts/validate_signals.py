#!/usr/bin/env python3
"""
信號驗證腳本 - 驗證六維系統和各指標準確率
"""
import json
import sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# 添加項目路徑
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import ccxt
except ImportError:
    print("需要安裝 ccxt: pip install ccxt")
    sys.exit(1)


def load_signals(hours=24):
    """載入信號記錄"""
    signals = []
    log_dir = Path("logs/whale_paper_trader")
    
    for f in sorted(log_dir.glob("signals_*.json"), reverse=True)[:20]:
        try:
            with open(f) as fp:
                data = json.load(fp)
                for s in data.get("signals", []):
                    if s.get("signal_type") == "ENTERED":
                        signals.append(s)
        except Exception as e:
            pass
    
    return signals


def validate_six_dim_accuracy(signals, exchange, lookahead_minutes=5):
    """驗證六維系統準確率"""
    if not signals:
        return None
    
    # 按時間排序
    signals.sort(key=lambda x: x["timestamp"])
    
    # 取最近的信號
    signals = signals[:500]
    
    # 獲取時間範圍
    first_time = datetime.fromisoformat(signals[0]["timestamp"].replace("Z", ""))
    last_time = datetime.fromisoformat(signals[-1]["timestamp"].replace("Z", ""))
    
    print(f"信號時間範圍: {first_time.strftime('%Y-%m-%d %H:%M')} ~ {last_time.strftime('%Y-%m-%d %H:%M')}")
    
    # 獲取 K 線數據
    since = int((first_time - timedelta(minutes=10)).timestamp() * 1000)
    ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1m", since=since, limit=1000)
    
    print(f"獲取 {len(ohlcv)} 根 K 線")
    
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
        "by_dimension": {
            "obi": {"correct": 0, "total": 0},
            "rsi": {"correct": 0, "total": 0},
            "macd": {"correct": 0, "total": 0},
            "volume": {"correct": 0, "total": 0},
            "trend": {"correct": 0, "total": 0},
            "momentum": {"correct": 0, "total": 0}
        },
        "profits": [],
        "losses": []
    }
    
    for sig in signals:
        try:
            sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
            sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
            
            entry_kline = kline_dict.get(sig_minute)
            future_minute = (sig_time + timedelta(minutes=lookahead_minutes)).strftime("%Y-%m-%d %H:%M")
            future_kline = kline_dict.get(future_minute)
            
            if not entry_kline or not future_kline:
                continue
            
            direction = sig.get("direction", "")
            six_dim = sig.get("six_dim", {})
            
            if not six_dim:
                continue
                
            score = max(six_dim.get("long_score", 0), six_dim.get("short_score", 0))
            
            entry_price = entry_kline["close"]
            future_price = future_kline["close"]
            change_pct = (future_price - entry_price) / entry_price * 100
            
            is_correct = (direction == "LONG" and change_pct > 0) or (direction == "SHORT" and change_pct < 0)
            
            results["total"] += 1
            results["by_score"][score]["total"] += 1
            
            if direction in results["by_direction"]:
                results["by_direction"][direction]["total"] += 1
            
            if is_correct:
                results["correct"] += 1
                results["by_score"][score]["correct"] += 1
                if direction in results["by_direction"]:
                    results["by_direction"][direction]["correct"] += 1
                results["profits"].append(abs(change_pct))
            else:
                results["losses"].append(abs(change_pct))
                
            # 驗證各維度
            dims = six_dim.get("dimensions", {})
            for dim_name, dim_value in dims.items():
                if dim_name in results["by_dimension"]:
                    results["by_dimension"][dim_name]["total"] += 1
                    # 判斷維度是否正確
                    dim_direction = "LONG" if dim_value > 0 else "SHORT" if dim_value < 0 else None
                    if dim_direction == direction and is_correct:
                        results["by_dimension"][dim_name]["correct"] += 1
                        
        except Exception as e:
            pass
    
    return results


def analyze_obi_accuracy(signals, exchange, lookahead_minutes=5):
    """分析 OBI 指標準確率"""
    if not signals:
        return None
    
    signals.sort(key=lambda x: x["timestamp"])
    signals = signals[:500]
    
    first_time = datetime.fromisoformat(signals[0]["timestamp"].replace("Z", ""))
    since = int((first_time - timedelta(minutes=10)).timestamp() * 1000)
    ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1m", since=since, limit=1000)
    
    kline_dict = {}
    for k in ohlcv:
        ts = datetime.fromtimestamp(k[0] / 1000)
        kline_dict[ts.strftime("%Y-%m-%d %H:%M")] = {
            "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]
        }
    
    # OBI 區間分析
    obi_ranges = {
        "strong_long": {"range": (0.1, 1.0), "correct": 0, "total": 0, "direction": "LONG"},
        "mild_long": {"range": (0.05, 0.1), "correct": 0, "total": 0, "direction": "LONG"},
        "neutral": {"range": (-0.05, 0.05), "correct": 0, "total": 0, "direction": None},
        "mild_short": {"range": (-0.1, -0.05), "correct": 0, "total": 0, "direction": "SHORT"},
        "strong_short": {"range": (-1.0, -0.1), "correct": 0, "total": 0, "direction": "SHORT"}
    }
    
    for sig in signals:
        try:
            sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
            sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
            
            entry_kline = kline_dict.get(sig_minute)
            future_minute = (sig_time + timedelta(minutes=lookahead_minutes)).strftime("%Y-%m-%d %H:%M")
            future_kline = kline_dict.get(future_minute)
            
            if not entry_kline or not future_kline:
                continue
            
            market = sig.get("market", {})
            obi = market.get("obi", 0)
            
            entry_price = entry_kline["close"]
            future_price = future_kline["close"]
            change_pct = (future_price - entry_price) / entry_price * 100
            
            # 分類 OBI
            for range_name, range_data in obi_ranges.items():
                low, high = range_data["range"]
                if low <= obi < high or (range_name == "strong_long" and obi >= high):
                    if range_name == "strong_short" and obi < low:
                        continue
                    range_data["total"] += 1
                    expected_dir = range_data["direction"]
                    if expected_dir:
                        actual_correct = (expected_dir == "LONG" and change_pct > 0) or (expected_dir == "SHORT" and change_pct < 0)
                        if actual_correct:
                            range_data["correct"] += 1
                    break
                    
        except Exception as e:
            pass
    
    return obi_ranges


def validate_rsi_accuracy(signals, exchange, lookahead_minutes=5):
    """驗證 RSI 指標準確率"""
    if not signals:
        return None
    
    signals.sort(key=lambda x: x["timestamp"])
    signals = signals[:500]
    
    first_time = datetime.fromisoformat(signals[0]["timestamp"].replace("Z", ""))
    since = int((first_time - timedelta(minutes=10)).timestamp() * 1000)
    ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1m", since=since, limit=1000)
    
    kline_dict = {}
    for k in ohlcv:
        ts = datetime.fromtimestamp(k[0] / 1000)
        kline_dict[ts.strftime("%Y-%m-%d %H:%M")] = {
            "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]
        }
    
    # RSI 區間分析
    rsi_ranges = {
        "oversold": {"range": (0, 30), "correct": 0, "total": 0, "expected": "LONG"},
        "low": {"range": (30, 45), "correct": 0, "total": 0, "expected": "LONG"},
        "neutral": {"range": (45, 55), "correct": 0, "total": 0, "expected": None},
        "high": {"range": (55, 70), "correct": 0, "total": 0, "expected": "SHORT"},
        "overbought": {"range": (70, 100), "correct": 0, "total": 0, "expected": "SHORT"}
    }
    
    for sig in signals:
        try:
            sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
            sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
            
            entry_kline = kline_dict.get(sig_minute)
            future_minute = (sig_time + timedelta(minutes=lookahead_minutes)).strftime("%Y-%m-%d %H:%M")
            future_kline = kline_dict.get(future_minute)
            
            if not entry_kline or not future_kline:
                continue
            
            mtf = sig.get("mtf", {})
            rsi = mtf.get("rsi_1m", 50)  # 默認 50
            
            entry_price = entry_kline["close"]
            future_price = future_kline["close"]
            change_pct = (future_price - entry_price) / entry_price * 100
            
            # 分類 RSI
            for range_name, range_data in rsi_ranges.items():
                low, high = range_data["range"]
                if low <= rsi < high:
                    range_data["total"] += 1
                    expected = range_data["expected"]
                    if expected:
                        actual_correct = (expected == "LONG" and change_pct > 0) or (expected == "SHORT" and change_pct < 0)
                        if actual_correct:
                            range_data["correct"] += 1
                    break
                    
        except Exception as e:
            pass
    
    return rsi_ranges


def save_calibration(results, obi_results, rsi_results):
    """保存校正結果到 JSON"""
    calibration = {
        "last_update": datetime.now().isoformat(),
        "validation_summary": {
            "total_signals": results["total"] if results else 0,
            "overall_accuracy": round(results["correct"] / results["total"] * 100, 1) if results and results["total"] > 0 else 0
        },
        "six_dim": {
            "enabled": True,
            "accuracy_by_score": {},
            "optimal_threshold": 8,
            "recommendations": []
        },
        "obi": {
            "enabled": True,
            "accuracy_by_range": {},
            "optimal_long_threshold": 0.065,
            "optimal_short_threshold": -0.057
        },
        "rsi": {
            "enabled": True,
            "accuracy_by_range": {}
        }
    }
    
    # 六維分數準確率
    if results:
        for score, data in sorted(results["by_score"].items(), reverse=True):
            if data["total"] >= 5:
                acc = round(data["correct"] / data["total"] * 100, 1)
                calibration["six_dim"]["accuracy_by_score"][str(score)] = {
                    "accuracy": acc,
                    "sample_size": data["total"]
                }
                if acc >= 60:
                    calibration["six_dim"]["recommendations"].append(f"分數 {score} 準確率 {acc}%，可信度高")
                elif acc <= 45:
                    calibration["six_dim"]["recommendations"].append(f"分數 {score} 準確率 {acc}%，需謹慎")
    
    # OBI 準確率
    if obi_results:
        for range_name, data in obi_results.items():
            if data["total"] >= 5:
                acc = round(data["correct"] / data["total"] * 100, 1) if data["correct"] > 0 else 0
                calibration["obi"]["accuracy_by_range"][range_name] = {
                    "accuracy": acc,
                    "sample_size": data["total"]
                }
    
    # RSI 準確率
    if rsi_results:
        for range_name, data in rsi_results.items():
            if data["total"] >= 5:
                acc = round(data["correct"] / data["total"] * 100, 1) if data["correct"] > 0 else 0
                calibration["rsi"]["accuracy_by_range"][range_name] = {
                    "accuracy": acc,
                    "sample_size": data["total"]
                }
    
    # 保存
    output_dir = Path("config/calibration")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / "signal_calibration.json"
    with open(output_file, "w") as f:
        json.dump(calibration, f, indent=2, ensure_ascii=False)
    
    print(f"\n📁 校正結果已保存: {output_file}")
    return calibration


def main():
    print("=" * 60)
    print("📊 信號準確率驗證系統")
    print("=" * 60)
    
    # 連接交易所
    print("\n🔗 連接幣安 API...")
    exchange = ccxt.binance({"timeout": 15000})
    
    # 載入信號
    print("\n📥 載入信號記錄...")
    signals = load_signals()
    print(f"共載入 {len(signals)} 筆進場信號")
    
    if not signals:
        print("❌ 沒有找到信號記錄")
        return
    
    # 驗證六維系統
    print("\n" + "=" * 60)
    print("🔍 驗證六維系統準確率 (前看 5 分鐘)")
    print("=" * 60)
    
    results = validate_six_dim_accuracy(signals.copy(), exchange, lookahead_minutes=5)
    
    if results and results["total"] > 0:
        accuracy = results["correct"] / results["total"] * 100
        print(f"\n總驗證信號: {results['total']}")
        print(f"正確預測: {results['correct']} ({accuracy:.1f}%)")
        print(f"錯誤預測: {results['total'] - results['correct']}")
        
        if results["profits"]:
            print(f"正確時平均獲利: {np.mean(results['profits']):.3f}%")
        if results["losses"]:
            print(f"錯誤時平均虧損: {np.mean(results['losses']):.3f}%")
        
        print("\n📈 按六維分數統計:")
        for score in sorted(results["by_score"].keys(), reverse=True):
            data = results["by_score"][score]
            if data["total"] > 0:
                acc = data["correct"] / data["total"] * 100
                emoji = "✅" if acc >= 55 else "⚠️" if acc >= 45 else "❌"
                print(f"   {emoji} {score}/12 分: {data['correct']}/{data['total']} ({acc:.1f}%)")
        
        print("\n📈 按方向統計:")
        for direction, data in results["by_direction"].items():
            if data["total"] > 0:
                acc = data["correct"] / data["total"] * 100
                emoji = "✅" if acc >= 55 else "⚠️" if acc >= 45 else "❌"
                print(f"   {emoji} {direction}: {data['correct']}/{data['total']} ({acc:.1f}%)")
    else:
        print("沒有足夠的數據進行驗證")
    
    # 驗證 OBI
    print("\n" + "=" * 60)
    print("🔍 驗證 OBI 指標準確率")
    print("=" * 60)
    
    obi_results = analyze_obi_accuracy(signals.copy(), exchange, lookahead_minutes=5)
    
    if obi_results:
        print("\n📊 OBI 區間準確率:")
        for range_name, data in obi_results.items():
            if data["total"] > 0:
                acc = data["correct"] / data["total"] * 100 if data["correct"] > 0 else 0
                emoji = "✅" if acc >= 55 else "⚠️" if acc >= 45 else "❌"
                dir_str = f"({data['direction']})" if data["direction"] else "(中性)"
                print(f"   {emoji} {range_name} {dir_str}: {data['correct']}/{data['total']} ({acc:.1f}%)")
    
    # 驗證 RSI
    print("\n" + "=" * 60)
    print("🔍 驗證 RSI 指標準確率")
    print("=" * 60)
    
    rsi_results = validate_rsi_accuracy(signals.copy(), exchange, lookahead_minutes=5)
    
    if rsi_results:
        print("\n📊 RSI 區間準確率:")
        for range_name, data in rsi_results.items():
            if data["total"] > 0:
                acc = data["correct"] / data["total"] * 100 if data["correct"] > 0 else 0
                emoji = "✅" if acc >= 55 else "⚠️" if acc >= 45 else "❌"
                expected_str = f"(預期{data['expected']})" if data["expected"] else "(中性)"
                print(f"   {emoji} {range_name} {expected_str}: {data['correct']}/{data['total']} ({acc:.1f}%)")
    
    # 保存校正結果
    print("\n" + "=" * 60)
    print("💾 保存校正結果")
    print("=" * 60)
    
    calibration = save_calibration(results, obi_results, rsi_results)
    
    # 給出建議
    print("\n" + "=" * 60)
    print("💡 優化建議")
    print("=" * 60)
    
    if results and results["total"] > 0:
        accuracy = results["correct"] / results["total"] * 100
        
        if accuracy >= 55:
            print("✅ 六維系統整體準確率良好")
        elif accuracy >= 45:
            print("⚠️ 六維系統準確率中等，建議提高分數門檻")
        else:
            print("❌ 六維系統準確率偏低，建議調整維度權重")
        
        # 找出最佳分數門檻
        best_score = None
        best_accuracy = 0
        for score in sorted(results["by_score"].keys(), reverse=True):
            data = results["by_score"][score]
            if data["total"] >= 10:
                acc = data["correct"] / data["total"] * 100
                if acc > best_accuracy:
                    best_accuracy = acc
                    best_score = score
        
        if best_score:
            print(f"📌 建議使用 {best_score}/12 分以上的信號 (準確率 {best_accuracy:.1f}%)")


if __name__ == "__main__":
    main()
