import json
from pathlib import Path

DATA_ROOT = Path("data/paper_trading")


def fmt_pct(num: float, den: int) -> str:
    if den == 0:
        return "N/A"
    return f"{(num / den) * 100:.2f}%"


def analyze_session(session_folder: str):
    folder = DATA_ROOT / session_folder
    json_path = folder / "trading_data.json"
    if not json_path.exists():
        print(f"❌ 找不到 {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    orders = data.get("orders", {})

    total_trades = 0
    global_follow_trades = 0
    global_follow_wins = 0
    global_follow_pnl = 0.0
    global_normal_trades = 0
    global_normal_wins = 0
    global_normal_pnl = 0.0

    per_mode_stats = {}

    for mode, mode_orders in orders.items():
        stats = {
            'follow_trades': 0,
            'follow_wins': 0,
            'follow_pnl': 0.0,
            'normal_trades': 0,
            'normal_wins': 0,
            'normal_pnl': 0.0,
        }

        for o in mode_orders:
            total_trades += 1
            pnl = float(o.get("pnl_usdt", 0.0))
            reason = o.get("entry_reason")

            if reason == "LARGE_TRADE_FOLLOW":
                stats['follow_trades'] += 1
                stats['follow_pnl'] += pnl
                global_follow_trades += 1
                global_follow_pnl += pnl
                if pnl > 0:
                    stats['follow_wins'] += 1
                    global_follow_wins += 1
            else:
                stats['normal_trades'] += 1
                stats['normal_pnl'] += pnl
                global_normal_trades += 1
                global_normal_pnl += pnl
                if pnl > 0:
                    stats['normal_wins'] += 1
                    global_normal_wins += 1

        per_mode_stats[mode] = stats

    print("================= 大單跟單績效報告 =================")
    print(f"Session: {session_folder}")
    print(f"總交易筆數: {total_trades}")
    print()

    # 全域總覽
    print("[全體模式匯總]")
    print("  — 大單跟單訂單 —")
    print(f"    筆數: {global_follow_trades}")
    print(f"    勝率: {fmt_pct(global_follow_wins, global_follow_trades)}")
    print(f"    總 PnL: {global_follow_pnl:+.4f} USDT")
    print(
        f"    平均每筆 PnL: {global_follow_pnl / global_follow_trades:.4f} USDT"
        if global_follow_trades > 0 else "    平均每筆 PnL: N/A"
    )
    print("  — 非大單跟單訂單 —")
    print(f"    筆數: {global_normal_trades}")
    print(f"    勝率: {fmt_pct(global_normal_wins, global_normal_trades)}")
    print(f"    總 PnL: {global_normal_pnl:+.4f} USDT")
    print(
        f"    平均每筆 PnL: {global_normal_pnl / global_normal_trades:.4f} USDT"
        if global_normal_trades > 0 else "    平均每筆 PnL: N/A"
    )
    print()

    # 各 mode 細分
    print("[各模式明細]")
    for mode, s in per_mode_stats.items():
        print(f"- 模式 {mode}")
        print("  大單跟單:")
        print(f"    筆數: {s['follow_trades']}")
        print(f"    勝率: {fmt_pct(s['follow_wins'], s['follow_trades'])}")
        print(f"    總 PnL: {s['follow_pnl']:+.4f} USDT")
        print(
            f"    平均每筆 PnL: {s['follow_pnl'] / s['follow_trades']:.4f} USDT"
            if s['follow_trades'] > 0 else "    平均每筆 PnL: N/A"
        )
        print("  非大單跟單:")
        print(f"    筆數: {s['normal_trades']}")
        print(f"    勝率: {fmt_pct(s['normal_wins'], s['normal_trades'])}")
        print(f"    總 PnL: {s['normal_pnl']:+.4f} USDT")
        print(
            f"    平均每筆 PnL: {s['normal_pnl'] / s['normal_trades']:.4f} USDT"
            if s['normal_trades'] > 0 else "    平均每筆 PnL: N/A"
        )
        print()

    print("=====================================================")


if __name__ == "__main__":
    # 預設用你剛剛的 24 小時 Session，可依需要改成其他資料夾
    default_session = "pt_20251118_1202"
    analyze_session(default_session)
