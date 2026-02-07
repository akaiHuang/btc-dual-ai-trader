#!/usr/bin/env python3
"""
🙏 台灣神明交易加持系統 v1.0
================================
追蹤祈求效果，用數據驗證哪個神明最有效！

使用方式:
  python scripts/god_blessing_system.py pray        # 祈求神明
  python scripts/god_blessing_system.py record      # 記錄交易結果
  python scripts/god_blessing_system.py stats       # 查看統計
  python scripts/god_blessing_system.py leaderboard # 神明排行榜
"""

import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import argparse

# 神明資料庫
GODS = {
    "媽祖": {"domain": "航海平安", "boost": "穩定獲利", "emoji": "🌊", "prayer": "媽祖娘娘保佑，航行順利，交易平安"},
    "關聖帝君": {"domain": "忠義財運", "boost": "提升勝率", "emoji": "⚔️", "prayer": "關聖帝君在上，忠義為本，財運亨通"},
    "土地公": {"domain": "財富土地", "boost": "小額穩賺", "emoji": "🏠", "prayer": "土地公伯保佑，財源廣進，穩紮穩打"},
    "財神爺": {"domain": "招財進寶", "boost": "大額獲利", "emoji": "💰", "prayer": "財神爺保佑，招財進寶，日進斗金"},
    "月老": {"domain": "姻緣人緣", "boost": "連勝運", "emoji": "💕", "prayer": "月老星君牽線，人緣財緣，連連勝利"},
    "城隍爺": {"domain": "司法公正", "boost": "避開詐騙", "emoji": "⚖️", "prayer": "城隍爺明察，邪不勝正，避開陷阱"},
    "玄天上帝": {"domain": "北極驅邪", "boost": "避黑天鵝", "emoji": "⚡", "prayer": "玄天上帝護佑，驅邪避凶，逢凶化吉"},
    "三太子": {"domain": "戰神勇猛", "boost": "高槓桿運", "emoji": "🔥", "prayer": "三太子神威，勇猛精進，大膽獲利"},
    "觀世音": {"domain": "慈悲救苦", "boost": "解套", "emoji": "🙏", "prayer": "觀世音菩薩，大慈大悲，救苦救難"},
    "濟公": {"domain": "癲狂智慧", "boost": "反向操作", "emoji": "🍶", "prayer": "濟公活佛，瘋癲有道，反向致富"},
    "王爺": {"domain": "驅邪除煞", "boost": "空頭獲利", "emoji": "👑", "prayer": "王爺千歲，驅邪除煞，轉運乾坤"},
    "保生大帝": {"domain": "醫療健康", "boost": "回血", "emoji": "💊", "prayer": "保生大帝，妙手回春，虧損回血"},
    "文昌帝君": {"domain": "學業智慧", "boost": "技術分析", "emoji": "📚", "prayer": "文昌帝君，智慧開啟，看透盤勢"},
    "註生娘娘": {"domain": "生育子嗣", "boost": "複利成長", "emoji": "🌱", "prayer": "註生娘娘，生生不息，複利滾滾"},
    "虎爺": {"domain": "財運守護", "boost": "快速獲利", "emoji": "🐯", "prayer": "虎爺威猛，咬錢進門，快速獲利"},
}

DATA_FILE = Path(__file__).parent.parent / "data" / "god_blessing_records.json"


def load_data() -> Dict:
    """載入祈求記錄"""
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "prayers": [],           # 祈求記錄
        "trade_results": [],     # 交易結果
        "god_stats": {},         # 神明統計
    }


def save_data(data: Dict):
    """儲存記錄"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def pray(god_name: Optional[str] = None):
    """🙏 祈求神明"""
    data = load_data()
    
    print("\n" + "=" * 60)
    print("🙏 台灣神明交易加持系統")
    print("=" * 60)
    
    if god_name is None:
        # 顯示神明列表
        print("\n📿 請選擇要祈求的神明:\n")
        gods_list = list(GODS.keys())
        for i, name in enumerate(gods_list, 1):
            info = GODS[name]
            print(f"  {i:>2}. {info['emoji']} {name:<8} - {info['boost']}")
        
        print(f"\n  {len(gods_list)+1}. 🎲 隨機 (讓神明選擇你)")
        print(f"  {len(gods_list)+2}. 📿 全部 (誠心祈求所有神明)")
        
        try:
            choice = input("\n請輸入數字選擇: ").strip()
            if choice == str(len(gods_list) + 1):
                god_name = random.choice(gods_list)
                print(f"\n🎲 神明選擇了你！")
            elif choice == str(len(gods_list) + 2):
                god_name = "ALL"
            else:
                idx = int(choice) - 1
                if 0 <= idx < len(gods_list):
                    god_name = gods_list[idx]
                else:
                    print("❌ 無效選擇")
                    return
        except (ValueError, KeyboardInterrupt):
            print("\n❌ 取消祈求")
            return
    
    # 執行祈求儀式
    if god_name == "ALL":
        print("\n" + "=" * 60)
        print("📿 誠心祈求所有神明...")
        print("=" * 60)
        for name, info in GODS.items():
            print(f"\n{info['emoji']} {name}: {info['prayer']}")
        prayer_record = {
            "god": "ALL",
            "time": datetime.now().isoformat(),
            "trades_after": [],
        }
    else:
        if god_name not in GODS:
            print(f"❌ 找不到神明: {god_name}")
            return
        
        info = GODS[god_name]
        print("\n" + "=" * 60)
        print(f"{info['emoji']} 祈求 {god_name}")
        print("=" * 60)
        
        # 祈求儀式
        print("\n🕯️ 點燃心燈...")
        print("🙏 雙手合十...")
        print(f"\n📜 祝禱文:")
        print(f"   「{info['prayer']}」")
        print("\n✨ 神明已收到您的祈求！")
        
        prayer_record = {
            "god": god_name,
            "time": datetime.now().isoformat(),
            "trades_after": [],
        }
    
    # 儲存祈求記錄
    data["prayers"].append(prayer_record)
    save_data(data)
    
    print("\n" + "=" * 60)
    print(f"✅ 祈求已記錄！")
    print(f"   接下來的交易結果將自動關聯到此次祈求")
    print(f"   使用 'python scripts/god_blessing_system.py record' 記錄結果")
    print("=" * 60)


def record_trade():
    """📝 記錄交易結果"""
    data = load_data()
    
    # 檢查是否有活躍的祈求
    recent_prayers = [p for p in data["prayers"] 
                     if datetime.fromisoformat(p["time"]) > datetime.now() - timedelta(hours=24)]
    
    if not recent_prayers:
        print("⚠️ 過去 24 小時內沒有祈求記錄")
        print("   請先使用 'python scripts/god_blessing_system.py pray' 祈求")
        return
    
    latest_prayer = recent_prayers[-1]
    god_name = latest_prayer["god"]
    
    print("\n" + "=" * 60)
    print(f"📝 記錄交易結果 (當前加持: {god_name})")
    print("=" * 60)
    
    try:
        n_trades = int(input("這次交易了幾筆? "))
        n_wins = int(input("其中贏了幾筆? "))
        pnl_pct = float(input("總損益 % (例如 +5.2 或 -3.1)? "))
    except (ValueError, KeyboardInterrupt):
        print("❌ 取消記錄")
        return
    
    # 記錄結果
    trade_record = {
        "time": datetime.now().isoformat(),
        "god": god_name,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "pnl_pct": pnl_pct,
        "win_rate": n_wins / n_trades if n_trades > 0 else 0,
    }
    
    data["trade_results"].append(trade_record)
    latest_prayer["trades_after"].append(trade_record)
    
    # 更新神明統計
    if god_name not in data["god_stats"]:
        data["god_stats"][god_name] = {
            "total_prayers": 0,
            "total_trades": 0,
            "total_wins": 0,
            "total_pnl": 0,
        }
    
    stats = data["god_stats"][god_name]
    stats["total_prayers"] += 1
    stats["total_trades"] += n_trades
    stats["total_wins"] += n_wins
    stats["total_pnl"] += pnl_pct
    
    save_data(data)
    
    # 顯示結果
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0
    emoji = "✅" if pnl_pct > 0 else "❌"
    
    print("\n" + "=" * 60)
    print(f"{emoji} 已記錄！")
    print(f"   交易: {n_trades} 筆")
    print(f"   勝率: {win_rate:.1f}%")
    print(f"   損益: {pnl_pct:+.2f}%")
    print(f"   神明: {god_name}")
    print("=" * 60)


def show_stats():
    """📊 查看統計"""
    data = load_data()
    
    print("\n" + "=" * 60)
    print("📊 祈求效果統計")
    print("=" * 60)
    
    total_prayers = len(data["prayers"])
    total_trades = len(data["trade_results"])
    
    print(f"\n總祈求次數: {total_prayers}")
    print(f"總記錄交易: {total_trades}")
    
    if not data["god_stats"]:
        print("\n⚠️ 還沒有足夠的數據，請繼續祈求和記錄！")
        return
    
    print("\n" + "-" * 60)
    print(f"{'神明':<12} {'祈求':>6} {'交易':>6} {'勝率':>8} {'總損益':>10}")
    print("-" * 60)
    
    for god_name, stats in sorted(data["god_stats"].items(), 
                                   key=lambda x: x[1]["total_pnl"], reverse=True):
        win_rate = stats["total_wins"] / stats["total_trades"] * 100 if stats["total_trades"] > 0 else 0
        emoji = GODS.get(god_name, {}).get("emoji", "🙏")
        print(f"{emoji} {god_name:<10} {stats['total_prayers']:>6} {stats['total_trades']:>6} "
              f"{win_rate:>7.1f}% {stats['total_pnl']:>+9.2f}%")


def show_leaderboard():
    """🏆 神明排行榜"""
    data = load_data()
    
    print("\n" + "=" * 60)
    print("🏆 神明效果排行榜 (基於實際交易數據)")
    print("=" * 60)
    
    if not data["god_stats"]:
        print("\n⚠️ 還沒有數據！")
        print("   1. 先祈求: python scripts/god_blessing_system.py pray")
        print("   2. 交易後記錄: python scripts/god_blessing_system.py record")
        return
    
    # 計算效果分數
    scored = []
    for god_name, stats in data["god_stats"].items():
        if stats["total_trades"] < 5:
            continue  # 至少 5 筆交易才算
        
        win_rate = stats["total_wins"] / stats["total_trades"]
        avg_pnl = stats["total_pnl"] / stats["total_prayers"] if stats["total_prayers"] > 0 else 0
        
        # 效果分數 = 勝率 * 0.4 + 平均損益 * 0.6
        score = win_rate * 40 + avg_pnl * 0.6
        
        scored.append({
            "name": god_name,
            "score": score,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "n_trades": stats["total_trades"],
        })
    
    if not scored:
        print("\n⚠️ 每個神明至少需要 5 筆交易記錄才能上榜")
        return
    
    scored.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"\n{'排名':<4} {'神明':<12} {'效果分數':>10} {'勝率':>8} {'平均損益':>10} {'樣本':>6}")
    print("-" * 60)
    
    for i, s in enumerate(scored, 1):
        emoji = GODS.get(s["name"], {}).get("emoji", "🙏")
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"{medal}{i:<2} {emoji} {s['name']:<10} {s['score']:>10.1f} "
              f"{s['win_rate']*100:>7.1f}% {s['avg_pnl']:>+9.2f}% {s['n_trades']:>6}")
    
    # 統計顯著性提醒
    print("\n" + "=" * 60)
    print("📈 統計顯著性說明")
    print("=" * 60)
    print("""
⚠️ 注意: 這是基於你的實際交易記錄！

要獲得統計顯著的結果，建議:
- 每個神明至少祈求 10 次
- 每次祈求後至少記錄 10 筆交易
- 總樣本 > 100 筆交易

當前最有效的神明是基於你的真實數據！
""")


def auto_sync():
    """🔄 自動同步交易記錄 (從 logs 讀取)"""
    data = load_data()
    
    # 檢查最近的祈求
    recent_prayers = [p for p in data["prayers"] 
                     if datetime.fromisoformat(p["time"]) > datetime.now() - timedelta(hours=24)]
    
    if not recent_prayers:
        return
    
    latest_prayer = recent_prayers[-1]
    prayer_time = datetime.fromisoformat(latest_prayer["time"])
    
    # 讀取交易記錄
    logs_dir = Path(__file__).parent.parent / "logs" / "whale_paper_trader"
    if not logs_dir.exists():
        return
    
    # 尋找祈求後的交易
    new_trades = []
    for trade_file in sorted(logs_dir.glob("trades_*.json")):
        try:
            with open(trade_file) as f:
                trade_data = json.load(f)
            
            for trade in trade_data.get("trades", []):
                trade_time = datetime.fromisoformat(trade.get("entry_time", ""))
                if trade_time > prayer_time:
                    new_trades.append(trade)
        except:
            continue
    
    if new_trades:
        n_wins = sum(1 for t in new_trades if t.get("pnl_pct", 0) > 0)
        total_pnl = sum(t.get("pnl_pct", 0) for t in new_trades)
        
        print(f"\n🔄 自動同步: 發現 {len(new_trades)} 筆新交易")
        print(f"   勝率: {n_wins/len(new_trades)*100:.1f}%")
        print(f"   總損益: {total_pnl:+.2f}%")


def main():
    parser = argparse.ArgumentParser(description="🙏 台灣神明交易加持系統")
    parser.add_argument("action", choices=["pray", "record", "stats", "leaderboard", "sync"],
                       help="執行動作: pray=祈求, record=記錄, stats=統計, leaderboard=排行榜")
    parser.add_argument("--god", "-g", help="指定神明名稱")
    
    args = parser.parse_args()
    
    if args.action == "pray":
        pray(args.god)
    elif args.action == "record":
        record_trade()
    elif args.action == "stats":
        show_stats()
    elif args.action == "leaderboard":
        show_leaderboard()
    elif args.action == "sync":
        auto_sync()


if __name__ == "__main__":
    main()
