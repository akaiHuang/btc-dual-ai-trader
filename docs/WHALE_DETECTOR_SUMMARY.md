# 🐋 主力策略偵測器總覽（程式/指標對應）

本檔總結現有主力策略偵測功能、對應程式路徑與關鍵指標特徵，方便快速對照與調試。

- 主要程式：`src/strategy/whale_strategy_detector.py`
- 詳細說明：`docs/WHALE_STRATEGY_DETECTOR_V2.md`

## 8 大主力策略（偵測完成）

| 策略 | 程式對應 | 核心指標特徵 (摘要) |
|------|----------|----------------------|
| 吸籌建倉 (ACCUMULATION) | `WhaleStrategyDetector.detect` | OBI 中性、VPIN 低、籌碼集中度高、量增價平 / 價量背離(累積)、隱藏買單 |
| 誘空吸籌 (BEAR_TRAP) | 同上 | OBI 偏空、WPI 正向、止損掃蕩高、假跌破(量縮)、下方隱藏買單 |
| 誘多派發 (BULL_TRAP) | 同上 | OBI 偏多、WPI 負向、止損掃蕩高、假突破(量縮)、上方隱藏賣單、空頭背離 |
| 拉高出貨 (PUMP_DUMP) | 同上 | OBI 強多、VPIN 高、巨量放大、價格 >1% 漲幅、上漲中量能衰竭、空頭背離 |
| 洗盤震倉 (SHAKE_OUT) | 同上 | 下跌中量能極度萎縮、止損掃蕩高、隱藏買單承接 |
| 試盤探測 (TESTING) | 同上 | 中等量能異動、小幅拉升/打壓、訊號強度低 |
| 對敲拉抬 (WASH_TRADING) | 同上 | 假成交放量 (量增但 VPIN/OBI 不支撐)、量能異常模式 |
| 砸盤打壓 (DUMP) | 同上 | OBI 強空、WPI 強負、瀑布式下跌、量先放後縮 |

## 主要偵測器類別與特徵

檔案：`src/strategy/whale_strategy_detector.py`

- `StopHuntDetector`（止損掃蕩）  
  長上下影/針狀 K 線，ATR 倍數超閾即記分；用於誘空/誘多判斷。
- `SupportResistanceBreakDetector`（支撐/壓力突破）  
  假突破/假跌破：突破關鍵位但量能不足 → 誘多/誘空訊號；回傳 is_likely_fake。
- `VolumeExhaustionDetector`（成交量衰竭）  
  趨勢內量能萎縮；上漲量縮 = 見頂風險，下降量縮 = 賣壓衰竭。
- `HiddenOrderDetector`（冰山單/隱單）  
  價位反覆被吃但掛單不減、成交量>>掛單量 → 隱藏買/賣單。
- `PriceVolumeDivergenceDetector`（價量背離）  
  價跌量縮=多頭背離、價漲量縮=空頭背離、量增價平=累積/派發。
- `WaterfallDropDetector`（瀑布式下跌）  
  連續 3+ 大陰線、跌幅>2%、量先放後縮 → 砸盤/恐慌賣壓。
- `OrderFlowDistortionDetector`（委託異動/掛單扭曲）  
  偵測快閃掛單/掛單深度異常，用於 spoofing/誘騙輔助判斷。
- `WhaleStrategyDetector`（總控）  
  彙總上述偵測器 + OBI/VPIN/WPI/液壓雷達/籌碼集中度，輸出策略機率、關鍵信號、風險警告。

## 常用指標與特徵欄位

- OBI/OBI Velocity：訂單簿失衡與變化率。
- VPIN：毒性風控；>0.6 停手或降權重。
- WPI (Whale Pressure Index)：鯨魚買賣壓 (whale_net_qty/whale_dominance)。
- Liquidation Pressure：爆倉壓力雷達，判斷瀑布/軋空風險。
- Volume Ratio / Volume Exhaustion：成交量相對均值，衰竭/爆量。
- Hidden Orders：冰山單、隱藏承接/派發。
- Price/Volume Divergence：價量背離型態。
- Support/Resistance Break Fake/True：假突破/假跌破標記。

> 若需完整特徵矩陣與權重，參考 `docs/WHALE_STRATEGY_DETECTOR_V2.md` 的「策略識別特徵矩陣」章節；程式細節集中在 `WhaleStrategyDetector.analyze()`。
