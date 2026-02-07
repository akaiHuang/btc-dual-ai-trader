#!/usr/bin/env python3
"""
新指標發現腳本 - 探索潛在的新交易指標
"""
import json
import sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import ccxt
except ImportError:
    print("需要安裝 ccxt: pip install ccxt")
    sys.exit(1)


def load_signals(hours=48):
    """載入信號記錄"""
    signals = []
    log_dir = Path("logs/whale_paper_trader")
    
    for f in sorted(log_dir.glob("signals_*.json"), reverse=True)[:30]:
        try:
            with open(f) as fp:
                data = json.load(fp)
                for s in data.get("signals", []):
                    if s.get("signal_type") == "ENTERED":
                        signals.append(s)
        except Exception as e:
            pass
    
    return signals


def analyze_indicator_correlations(signals, exchange):
    """分析指標之間的相關性"""
    print("\n📊 指標相關性分析")
    print("-" * 50)
    
    # 收集指標數據
    data = {
        'six_dim_score': [],
        'obi': [],
        'direction': [],
        'rsi_1m': [],
    }
    
    for sig in signals:
        six_dim = sig.get('six_dim', {})
        market = sig.get('market', {})
        mtf = sig.get('mtf', {})
        
        score = max(six_dim.get('long_score', 0), six_dim.get('short_score', 0))
        data['six_dim_score'].append(score)
        data['obi'].append(market.get('obi', 0))
        data['direction'].append(1 if sig.get('direction') == 'LONG' else -1)
        data['rsi_1m'].append(mtf.get('rsi_1m', 50))
    
    # 計算相關性
    if len(data['six_dim_score']) > 10:
        score_arr = np.array(data['six_dim_score'])
        obi_arr = np.array(data['obi'])
        dir_arr = np.array(data['direction'])
        
        # OBI 與方向的相關性
        obi_dir_corr = np.corrcoef(obi_arr, dir_arr)[0, 1]
        print(f"   OBI vs 方向: {obi_dir_corr:.3f}")
        
        # 六維分數與 OBI
        score_obi_corr = np.corrcoef(score_arr, np.abs(obi_arr))[0, 1]
        print(f"   六維分數 vs |OBI|: {score_obi_corr:.3f}")


def discover_new_indicators(signals, exchange):
    """發現新指標機會"""
    print("\n🔍 新指標發現")
    print("=" * 60)
    
    # 獲取 K 線數據用於驗證
    signals_sorted = sorted(signals, key=lambda x: x['timestamp'])[:500]
    
    if not signals_sorted:
        print("沒有足夠的信號數據")
        return {}
    
    first_time = datetime.fromisoformat(signals_sorted[0]["timestamp"].replace("Z", ""))
    since = int((first_time - timedelta(minutes=10)).timestamp() * 1000)
    
    try:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1m", since=since, limit=1000)
    except Exception as e:
        print(f"獲取 K 線失敗: {e}")
        return {}
    
    # 建立時間索引
    kline_dict = {}
    for k in ohlcv:
        ts = datetime.fromtimestamp(k[0] / 1000)
        kline_dict[ts.strftime("%Y-%m-%d %H:%M")] = {
            "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]
        }
    
    # 新指標測試
    new_indicators = {}
    
    # 1. OBI-價格背離指標
    print("\n📈 1. OBI-價格背離指標")
    print("-" * 40)
    
    divergence_results = {"correct": 0, "total": 0}
    
    for sig in signals_sorted:
        try:
            market = sig.get('market', {})
            obi = market.get('obi', 0)
            
            sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
            sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
            future_minute = (sig_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
            
            entry_kline = kline_dict.get(sig_minute)
            future_kline = kline_dict.get(future_minute)
            
            if not entry_kline or not future_kline:
                continue
            
            entry_price = entry_kline['close']
            future_price = future_kline['close']
            price_change = (future_price - entry_price) / entry_price
            
            # 背離信號: OBI > 0.1 但價格在跌 → 預期反彈
            if obi > 0.1 and entry_kline['close'] < entry_kline['open']:
                divergence_results["total"] += 1
                if future_price > entry_price:  # 5分鐘後漲
                    divergence_results["correct"] += 1
            
            # 背離信號: OBI < -0.1 但價格在漲 → 預期回落
            elif obi < -0.1 and entry_kline['close'] > entry_kline['open']:
                divergence_results["total"] += 1
                if future_price < entry_price:  # 5分鐘後跌
                    divergence_results["correct"] += 1
                    
        except Exception as e:
            pass
    
    if divergence_results["total"] > 0:
        acc = divergence_results["correct"] / divergence_results["total"] * 100
        print(f"   背離信號: {divergence_results['correct']}/{divergence_results['total']} ({acc:.1f}%)")
        new_indicators["obi_price_divergence"] = {
            "accuracy": acc,
            "sample_size": divergence_results["total"],
            "description": "OBI與價格方向背離時的反轉信號"
        }
    else:
        print("   沒有足夠的背離信號")
    
    # 2. 六維完美對齊指標 (12/12)
    print("\n📈 2. 六維完美對齊指標 (12分)")
    print("-" * 40)
    
    perfect_results = {"correct": 0, "total": 0}
    
    for sig in signals_sorted:
        try:
            six_dim = sig.get('six_dim', {})
            score = max(six_dim.get('long_score', 0), six_dim.get('short_score', 0))
            direction = sig.get('direction', '')
            
            if score >= 12:
                sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
                sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
                future_minute = (sig_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
                
                entry_kline = kline_dict.get(sig_minute)
                future_kline = kline_dict.get(future_minute)
                
                if not entry_kline or not future_kline:
                    continue
                
                entry_price = entry_kline['close']
                future_price = future_kline['close']
                
                perfect_results["total"] += 1
                is_correct = (direction == "LONG" and future_price > entry_price) or \
                            (direction == "SHORT" and future_price < entry_price)
                if is_correct:
                    perfect_results["correct"] += 1
                    
        except Exception as e:
            pass
    
    if perfect_results["total"] > 0:
        acc = perfect_results["correct"] / perfect_results["total"] * 100
        print(f"   完美對齊信號: {perfect_results['correct']}/{perfect_results['total']} ({acc:.1f}%)")
        new_indicators["perfect_alignment"] = {
            "accuracy": acc,
            "sample_size": perfect_results["total"],
            "description": "六維分數達到12/12的高信心信號"
        }
    else:
        print("   沒有找到12分信號")
    
    # 3. OBI 強度突變指標
    print("\n📈 3. OBI 強度突變指標 (|OBI| > 0.2)")
    print("-" * 40)
    
    strong_obi_results = {"correct": 0, "total": 0}
    
    for sig in signals_sorted:
        try:
            market = sig.get('market', {})
            obi = market.get('obi', 0)
            direction = sig.get('direction', '')
            
            # 強OBI信號
            if abs(obi) > 0.2:
                sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
                sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
                future_minute = (sig_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
                
                entry_kline = kline_dict.get(sig_minute)
                future_kline = kline_dict.get(future_minute)
                
                if not entry_kline or not future_kline:
                    continue
                
                entry_price = entry_kline['close']
                future_price = future_kline['close']
                
                strong_obi_results["total"] += 1
                
                # OBI > 0.2 預期漲, OBI < -0.2 預期跌
                expected_long = obi > 0.2
                is_correct = (expected_long and future_price > entry_price) or \
                            (not expected_long and future_price < entry_price)
                if is_correct:
                    strong_obi_results["correct"] += 1
                    
        except Exception as e:
            pass
    
    if strong_obi_results["total"] > 0:
        acc = strong_obi_results["correct"] / strong_obi_results["total"] * 100
        print(f"   強OBI信號: {strong_obi_results['correct']}/{strong_obi_results['total']} ({acc:.1f}%)")
        new_indicators["strong_obi"] = {
            "accuracy": acc,
            "sample_size": strong_obi_results["total"],
            "description": "|OBI| > 0.2 的強方向信號"
        }
    else:
        print("   沒有找到強OBI信號")
    
    # 4. SHORT + 高分組合
    print("\n📈 4. SHORT + 高分組合 (>=10分)")
    print("-" * 40)
    
    short_high_results = {"correct": 0, "total": 0}
    
    for sig in signals_sorted:
        try:
            six_dim = sig.get('six_dim', {})
            short_score = six_dim.get('short_score', 0)
            direction = sig.get('direction', '')
            
            if direction == "SHORT" and short_score >= 10:
                sig_time = datetime.fromisoformat(sig["timestamp"].replace("Z", ""))
                sig_minute = sig_time.strftime("%Y-%m-%d %H:%M")
                future_minute = (sig_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
                
                entry_kline = kline_dict.get(sig_minute)
                future_kline = kline_dict.get(future_minute)
                
                if not entry_kline or not future_kline:
                    continue
                
                entry_price = entry_kline['close']
                future_price = future_kline['close']
                
                short_high_results["total"] += 1
                if future_price < entry_price:
                    short_high_results["correct"] += 1
                    
        except Exception as e:
            pass
    
    if short_high_results["total"] > 0:
        acc = short_high_results["correct"] / short_high_results["total"] * 100
        print(f"   SHORT高分信號: {short_high_results['correct']}/{short_high_results['total']} ({acc:.1f}%)")
        new_indicators["short_high_score"] = {
            "accuracy": acc,
            "sample_size": short_high_results["total"],
            "description": "SHORT方向 + 六維分數>=10的組合"
        }
    else:
        print("   沒有找到SHORT高分信號")
    
    return new_indicators


def main():
    print("=" * 60)
    print("🔍 新指標發現系統")
    print("=" * 60)
    
    # 連接交易所
    print("\n🔗 連接幣安 API...")
    exchange = ccxt.binance({"timeout": 15000})
    
    # 載入信號
    print("📥 載入信號記錄...")
    signals = load_signals()
    print(f"共載入 {len(signals)} 筆進場信號")
    
    if not signals:
        print("❌ 沒有找到信號記錄")
        return
    
    # 發現新指標
    new_indicators = discover_new_indicators(signals, exchange)
    
    # 分析相關性
    analyze_indicator_correlations(signals, exchange)
    
    # 保存結果
    print("\n" + "=" * 60)
    print("💾 保存發現結果")
    print("=" * 60)
    
    # 更新校正配置
    calibration_file = Path("config/calibration/signal_calibration.json")
    if calibration_file.exists():
        with open(calibration_file) as f:
            calibration = json.load(f)
    else:
        calibration = {}
    
    calibration["new_indicators_discovery"] = {
        "last_update": datetime.now().isoformat(),
        "indicators": new_indicators
    }
    
    with open(calibration_file, "w") as f:
        json.dump(calibration, f, indent=2, ensure_ascii=False)
    
    print(f"📁 已更新: {calibration_file}")
    
    # 給出建議
    print("\n" + "=" * 60)
    print("💡 新指標建議")
    print("=" * 60)
    
    for name, data in new_indicators.items():
        acc = data.get("accuracy", 0)
        sample = data.get("sample_size", 0)
        desc = data.get("description", "")
        
        if acc >= 60 and sample >= 10:
            print(f"✅ {name}: {acc:.1f}% (n={sample})")
            print(f"   → {desc}")
            print(f"   → 建議: 加入交易策略")
        elif acc >= 50:
            print(f"⚠️ {name}: {acc:.1f}% (n={sample})")
            print(f"   → {desc}")
            print(f"   → 建議: 繼續觀察")
        else:
            print(f"❌ {name}: {acc:.1f}% (n={sample})")
            print(f"   → 建議: 不採用")


if __name__ == "__main__":
    main()
