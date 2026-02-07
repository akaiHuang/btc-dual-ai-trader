# 📋 TradingConfig 參數變更日誌

> 此文件記錄交易策略參數的所有變更歷史，現在參數統一由 `config/trading_cards/` 管理

---

## v13.6 (2025-01-11) 🎴 卡片系統完成
- 所有參數移至 JSON 卡片系統
- 移除 `TradingConfig` 中的 hardcode 預設值
- 預設從 `master_config.json` 的 `active_card` 載入

---

## v13.4 (2025-01-10) 🔧 六維評分修復
- 修復 `six_dim.get('score')` BUG (應為 `long_score`/`short_score`)
- `six_dim_min_score_to_trade`: 2 → 8 (提高門檻)

---

## v13.3 放寬初步止損
- `profit_lock_stages[2]`: -1.0 → -2.0 (剛轉正允許 -2% 回撤)
- 避免被雜訊震出場

---

## v13.1 加快信號確認
- `signal_confirm_seconds`: 5 → 2 秒
- `six_dim_alignment_threshold`: 4 → 6 (50%)

---

## v13.0 提高進場門檻
- `min_probability`: 0.15 → 0.50 → 0.70
- `min_confidence`: 0.12 → 0.25 → 0.60
- `min_signal_advantage`: 0.05 → 0.15
- `obi_long_threshold`: 0.03 → 0.10
- `obi_strong_threshold`: 0.20 → 0.25

---

## v12.12 價格確認重啟
- `price_confirm_enabled`: true (重新啟用)
- `price_confirm_threshold`: 0.01 → 0.03%

---

## v12.11 Warm-up + 動能確認
- 新增 `warmup_seconds`: 30.0
- 新增 `require_momentum_confirm`: true
- `contextual_mode`: true (六維信號競爭系統)

---

## v12.10 急跌急漲偵測
- 新增 `price_spike_enabled`: true
- 新增 `price_spike_threshold_pct`: 0.25%
- 新增 `price_spike_window_sec`: 60 秒

---

## v12.9 dYdX 數據源
- 改用 dYdX WebSocket + REST API
- `maker_fee_pct`: 0.005 (Maker)
- `taker_fee_pct`: 0.04 (Taker)

---

## v12.8 N%鎖N% 策略 (+1700% 回測改善)
- 新增 `use_n_lock_n`: true
- 新增 `n_lock_n_threshold`: 1.0
- 新增 `n_lock_n_buffer`: 0.0

---

## v12.2 階段性鎖利策略
- 新增 `profit_lock_stages` 動態止損陣列
- 核心原則: 止損永遠 ≤ 止盈

---

## v12.0 預掛單模式 (Maker)
- 新增 `pre_entry_mode`: true
- 新增 `pre_entry_threshold`: 0.90
- 新增 `pre_entry_price_offset`: 8.0 USD

---

## v11.1 修正手續費陷阱
- `target_profit_pct`: 0.25 → 0.40%
- `stop_loss_pct`: 0.12 → 0.20%
- `max_hold_minutes`: 15 → 30 分鐘

---

## v10.20 槓桿優化
- `leverage`: 100 → 50X
- 減少手續費影響 4% → 2%

---

## v10.16 六維信號系統
新增三維 (在原三線基礎上):
- OBI 線: ±2 分 (訂單簿失衡)
- 動能線: ±2 分 (價格動能)
- 成交量線: ±2 分 (大單方向)
- 總分: 12 分

---

## v10.15 縮短交易間隔
- `min_trade_interval_sec`: 5 → 1 秒

---

## v10.10 快線窗口優化
- `fast_window_seconds`: 10 → 5 秒
- `medium_window_seconds`: 60 → 30 秒

---

## v10.9 兩階段止盈止損
- 新增 `two_phase_exit_enabled`: true
- Phase 1: 費用突破期
- Phase 2: 鎖利期 (trailing stop)

---

## v10.3 專屬獲利模式
基於 103 筆歷史交易優化:
- MODE_A: OBI>0.4 + 價格微跌 + LONG [83.3%勝率]
- MODE_C: OBI中性 + SHORT + 機率80-90% [100%勝率]
- 新增 `ctx_*` 參數群

---

## v8.0 MTF-First 策略
- 新增 `mtf_first_mode`
- `mtf_hold_minutes`: 15 分鐘
- RSI 過濾: 30-65 (LONG), 35-70 (SHORT)

---

## v7.0 反向交易模式
- 新增 `reverse_mode`: LONG↔SHORT 互換

---

## v5.9 無動能快速止損
- 新增 `no_momentum_enabled`: true
- 發現: 92% 的「進場後從未漲超過 1%」交易最終虧損

---

## v3.0 反轉策略
- 新增 `reversal_mode_enabled`
- 主力假象消退進場

---

## v2.0 雙週期策略
- 快線 + 慢線分析
- 策略 Hysteresis (持續時間條件)
