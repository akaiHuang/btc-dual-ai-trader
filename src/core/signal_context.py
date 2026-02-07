"""
信號上下文 (Signal Context)

統一的數據結構，貫穿 L0-L3 四層架構
所有策略、AI 模型、風控模組都使用這個共享上下文
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from enum import Enum


class MarketRegime(Enum):
    """市場狀態"""
    BULL = "BULL"           # 強勢上漲
    BEAR = "BEAR"           # 強勢下跌
    RANGE = "RANGE"         # 區間震盪
    CRASH = "CRASH"         # 崩盤模式


class Direction(Enum):
    """方向"""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class ImpactLevel(Enum):
    """影響程度"""
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CycleRegime(Enum):
    """BTC 減半週期階段"""
    PRE_HALVING_ACCUMULATION = "pre_halving_accumulation"     # 減半前 365-180 天
    PRE_HALVING_HYPE = "pre_halving_hype"                     # 減半前 180-0 天
    POST_HALVING_PRICE_DISCOVERY = "post_halving_price_discovery"  # 減半後 0-180 天
    POST_HALVING_DISTRIBUTION = "post_halving_distribution"   # 減半後 180-540 天
    LATE_CYCLE = "late_cycle"                                 # 其他階段


@dataclass
class NewsFactor:
    """
    新聞/KOL 事件因子
    由 LLM 解析推文/新聞產生
    """
    asset: str = "BTC"
    direction: Direction = Direction.NEUTRAL  # 看多/看空/中性
    confidence: float = 0.0                   # LLM 信心度 (0-1)
    impact_level: ImpactLevel = ImpactLevel.NONE
    time_horizon: str = "intra-day"           # scalp / intra-day / swing
    tags: List[str] = field(default_factory=list)  # ['ETF_flow', 'regulation']
    source_influence: float = 0.5             # 消息來源影響力 (0-1)
    news_strength: float = 0.0                # confidence * source_influence
    
    def __post_init__(self):
        """自動計算 news_strength"""
        self.news_strength = self.confidence * self.source_influence


@dataclass
class SignalContext:
    """
    信號上下文 - 四層架構的核心數據結構
    
    這個類別包含了從 L0 到 L3 所有層級需要的數據：
    - L0: 多維度市場數據（訂單簿/資金流/鏈上/衍生品）
    - L1: 策略層需要的技術指標和市場狀態
    - L2: AI/LLM 層的智能分析結果
    - L3: 風控層的風險參數
    
    所有策略、模型、風控模組都共享這個上下文
    """
    
    # ========== 基礎信息 ==========
    timestamp: datetime
    symbol: str = "BTCUSDT"
    current_price: float = 0.0
    
    # ========== L0: 數據層（技術指標） ==========
    # 趨勢類
    ma_7: float = 0.0
    ma_25: float = 0.0
    ma_50: float = 0.0
    ma_200: float = 0.0
    ma_distance: float = 0.0           # 價格與 MA 距離百分比
    
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    
    adx: float = 0.0                   # 趨勢強度
    supertrend: float = 0.0
    
    # 動量類
    rsi: float = 50.0
    rsi_7: float = 50.0
    rsi_21: float = 50.0
    stoch_rsi: float = 50.0
    cci: float = 0.0
    williams_r: float = -50.0
    mfi: float = 50.0                  # Money Flow Index
    
    # 波動類
    atr: float = 0.0
    atr_percentile: float = 0.5        # ATR 在歷史上的分位數
    bb_upper: float = 0.0              # Bollinger Bands
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    
    # 成交量類
    volume: float = 0.0
    volume_ratio: float = 1.0          # 當前成交量 / 平均成交量
    obv: float = 0.0                   # On Balance Volume
    vwap: float = 0.0
    vwap_deviation: float = 0.0        # 價格偏離 VWAP 百分比
    
    # ========== L0: 數據層（訂單簿微觀結構） ==========
    obi: float = 0.0                   # Order Book Imbalance (-1 ~ +1)
    spread_bps: float = 0.0            # Bid-Ask Spread (basis points)
    bid_depth_total: float = 0.0       # 買盤深度（±2% 價格區間）
    ask_depth_total: float = 0.0       # 賣盤深度
    bid_depth_change_rate: float = 0.0 # 買盤深度變化率（vs 5秒前）
    ask_depth_change_rate: float = 0.0 # 賣盤深度變化率
    large_orders_direction: Direction = Direction.NEUTRAL  # 大單方向
    withdrawn_orders_count: int = 0    # 撤單數量（假單誘導）
    
    # ========== L0: 數據層（成交流 Tape） ==========
    taker_ratio: float = 1.0           # Taker Buy / Sell Volume Ratio
    aggressive_buy_volume: float = 0.0 # 主動買入量
    aggressive_sell_volume: float = 0.0 # 主動賣出量
    large_trades_direction: Direction = Direction.NEUTRAL  # 大單成交方向
    is_accumulating: bool = False      # 是否在吸籌
    is_distributing: bool = False      # 是否在出貨
    
    # ========== L0: 數據層（衍生品） ==========
    funding_rate: float = 0.0          # Funding Rate
    funding_rate_8h_avg: float = 0.0   # 8 小時平均
    funding_rate_24h_avg: float = 0.0  # 24 小時平均
    
    open_interest: float = 0.0         # 當前未平倉量
    oi_change_rate: float = 0.0        # OI 變化率（vs 1h前）
    oi_at_high_level: bool = False     # OI 是否在高位
    
    perp_spot_basis: float = 0.0       # 永續 vs 現貨價差 (bps)
    basis_percentile: float = 0.5      # 價差在歷史上的分位數
    
    liquidation_heatmap: Dict[float, float] = field(default_factory=dict)  # 各價位清算密度
    recent_liquidations_volume: float = 0.0  # 最近清算量
    liquidation_direction: Direction = Direction.NEUTRAL  # 清算方向
    price_breaks_long_liq_zone: bool = False   # 是否跌破多單清算區
    price_breaks_short_liq_zone: bool = False  # 是否突破空單清算區
    
    # ========== L0: 數據層（鏈上） ==========
    exchange_inflow_24h: float = 0.0   # 24h 流入交易所的 BTC
    exchange_outflow_24h: float = 0.0  # 24h 流出交易所的 BTC
    net_flow: float = 0.0              # 淨流動（正=流入=拋壓，負=流出=持有）
    
    whale_movements: List[Dict] = field(default_factory=list)  # 巨鯨轉移記錄
    whale_alert_level: ImpactLevel = ImpactLevel.NONE
    whale_alert_level_numeric: float = 0.0  # 數值化: 0/1/2/3
    
    miner_outflow: float = 0.0         # 礦工轉出量
    stablecoin_supply_change: float = 0.0  # Stablecoin 供應量變化
    
    # ========== L1: 策略層 ==========
    market_regime: MarketRegime = MarketRegime.RANGE
    market_regime_numeric: int = 0     # 數值化: 0=RANGE, 1=BULL, 2=BEAR, 3=CRASH
    
    is_consolidation: bool = False     # 是否盤整
    consolidation_duration: int = 0    # 盤整持續時間（分鐘）
    
    tech_edge_score: float = 50.0      # 技術指標評分 (0-100)
    orderflow_edge_score: float = 50.0 # 資金流評分 (0-100)
    
    signal_direction: Direction = Direction.NEUTRAL  # 建議方向
    signal_strength: float = 0.0       # 信號強度 (0-100)
    
    # ========== L2: AI/LLM 層 ==========
    # 新聞因子
    news_factor: Optional[NewsFactor] = None
    news_bias: int = 0                 # -1 (看空) / 0 (中性) / +1 (看多)
    news_strength: float = 0.0         # 0.0 - 1.0
    news_impact_level: ImpactLevel = ImpactLevel.NONE
    news_impact_level_numeric: float = 0.0  # 0/1/2/3
    event_risk_level: ImpactLevel = ImpactLevel.NONE
    
    # 週期判斷
    cycle_regime: CycleRegime = CycleRegime.LATE_CYCLE
    cycle_regime_numeric: int = 0      # 0-4
    days_since_halving: int = 0
    days_to_halving: int = 0
    
    # ML 勝率預估
    p_win: float = 0.5                 # ML 預估勝率 (0-1)
    confidence_level: str = "low"      # low / medium / high
    
    # ========== L3: 風控層 ==========
    volatility_percentile: float = 0.5 # 當前波動率分位數 (0-1)
    max_leverage: int = 5              # 允許的最大槓桿
    recommended_leverage: int = 3      # 建議槓桿
    
    risk_level: str = "medium"         # low / medium / high / extreme
    
    # 時間特徵（ML 用）
    hour_of_day: int = 0               # 0-23
    day_of_week: int = 0               # 0-6 (Monday=0)
    
    # ========== 輔助方法 ==========
    
    def __post_init__(self):
        """自動處理一些衍生計算"""
        # 數值化 enum
        self._convert_enums_to_numeric()
        
        # 從 news_factor 提取信息
        if self.news_factor:
            self.news_bias = self._direction_to_bias(self.news_factor.direction)
            self.news_strength = self.news_factor.news_strength
            self.news_impact_level = self.news_factor.impact_level
            self.news_impact_level_numeric = self._impact_to_numeric(self.news_factor.impact_level)
    
    def _convert_enums_to_numeric(self):
        """轉換 enum 為數值（ML 模型用）"""
        regime_map = {
            MarketRegime.RANGE: 0,
            MarketRegime.BULL: 1,
            MarketRegime.BEAR: 2,
            MarketRegime.CRASH: 3,
        }
        self.market_regime_numeric = regime_map.get(self.market_regime, 0)
        
        cycle_map = {
            CycleRegime.LATE_CYCLE: 0,
            CycleRegime.PRE_HALVING_ACCUMULATION: 1,
            CycleRegime.PRE_HALVING_HYPE: 2,
            CycleRegime.POST_HALVING_PRICE_DISCOVERY: 3,
            CycleRegime.POST_HALVING_DISTRIBUTION: 4,
        }
        self.cycle_regime_numeric = cycle_map.get(self.cycle_regime, 0)
        
        self.whale_alert_level_numeric = self._impact_to_numeric(self.whale_alert_level)
    
    @staticmethod
    def _direction_to_bias(direction: Direction) -> int:
        """Direction → bias"""
        if direction == Direction.LONG:
            return 1
        elif direction == Direction.SHORT:
            return -1
        else:
            return 0
    
    @staticmethod
    def _impact_to_numeric(impact: ImpactLevel) -> float:
        """ImpactLevel → numeric"""
        impact_map = {
            ImpactLevel.NONE: 0.0,
            ImpactLevel.LOW: 1.0,
            ImpactLevel.MEDIUM: 2.0,
            ImpactLevel.HIGH: 3.0,
        }
        return impact_map.get(impact, 0.0)
    
    def set_news_factor(self, news_factor: NewsFactor):
        """設置新聞因子並自動更新相關欄位"""
        self.news_factor = news_factor
        self.news_bias = self._direction_to_bias(news_factor.direction)
        self.news_strength = news_factor.news_strength
        self.news_impact_level = news_factor.impact_level
        self.news_impact_level_numeric = self._impact_to_numeric(news_factor.impact_level)
        
        # 高衝擊事件觸發 event_risk_level
        if news_factor.impact_level in [ImpactLevel.HIGH, ImpactLevel.MEDIUM]:
            self.event_risk_level = news_factor.impact_level
    
    def to_ml_features(self) -> List[float]:
        """
        轉換為 ML 模型特徵向量
        用於 WinratePredictorML
        """
        return [
            # 技術指標
            self.rsi, self.macd, self.ma_distance,
            self.atr_percentile, self.volume_ratio,
            self.adx, self.cci, self.mfi,
            
            # 訂單簿
            self.obi, self.spread_bps, self.bid_depth_change_rate,
            self.ask_depth_change_rate,
            
            # 成交流
            self.taker_ratio, self.aggressive_buy_volume / (self.aggressive_sell_volume + 1e-9),
            
            # 衍生品
            self.funding_rate, self.oi_change_rate, self.perp_spot_basis,
            
            # 鏈上
            self.net_flow, self.whale_alert_level_numeric,
            self.stablecoin_supply_change,
            
            # 新聞
            float(self.news_bias), self.news_strength,
            self.news_impact_level_numeric,
            
            # 市場狀態
            float(self.market_regime_numeric),
            float(self.is_consolidation),
            float(self.cycle_regime_numeric),
            
            # 評分
            self.tech_edge_score, self.orderflow_edge_score,
            
            # 時間
            float(self.hour_of_day), float(self.day_of_week),
            self.volatility_percentile,
        ]
    
    def to_dict(self) -> dict:
        """轉換為 dict（用於記錄/回測）"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'symbol': self.symbol,
            'current_price': self.current_price,
            
            # 技術指標
            'rsi': self.rsi,
            'macd': self.macd,
            'ma_distance': self.ma_distance,
            'atr': self.atr,
            'volume_ratio': self.volume_ratio,
            
            # 訂單簿
            'obi': self.obi,
            'spread_bps': self.spread_bps,
            'taker_ratio': self.taker_ratio,
            
            # 衍生品
            'funding_rate': self.funding_rate,
            'oi_change_rate': self.oi_change_rate,
            'perp_spot_basis': self.perp_spot_basis,
            
            # 鏈上
            'net_flow': self.net_flow,
            'whale_alert_level': self.whale_alert_level.value,
            
            # 策略
            'market_regime': self.market_regime.value,
            'is_consolidation': self.is_consolidation,
            'tech_edge_score': self.tech_edge_score,
            'orderflow_edge_score': self.orderflow_edge_score,
            
            # AI/LLM
            'news_bias': self.news_bias,
            'news_strength': self.news_strength,
            'cycle_regime': self.cycle_regime.value,
            'p_win': self.p_win,
            
            # 風控
            'volatility_percentile': self.volatility_percentile,
            'max_leverage': self.max_leverage,
        }
    
    def __repr__(self) -> str:
        """簡潔的字符串表示"""
        return (
            f"SignalContext({self.timestamp.strftime('%Y-%m-%d %H:%M')}, "
            f"price={self.current_price:.2f}, "
            f"regime={self.market_regime.value}, "
            f"edge={self.tech_edge_score:.1f}, "
            f"p_win={self.p_win:.2%})"
        )


# ========== 使用示例 ==========

if __name__ == "__main__":
    from datetime import datetime
    
    # 創建一個完整的上下文
    context = SignalContext(
        timestamp=datetime.now(),
        symbol="BTCUSDT",
        current_price=42000.0,
        
        # L0 數據
        rsi=35.0,
        macd=120.5,
        ma_distance=-0.02,
        atr=500.0,
        volume_ratio=1.5,
        
        obi=0.65,  # 買盤優勢
        spread_bps=2.5,
        taker_ratio=1.4,  # 主動買入強
        
        funding_rate=0.03,
        oi_change_rate=0.15,
        
        net_flow=-800,  # 流出交易所（持有）
        whale_alert_level=ImpactLevel.MEDIUM,
        
        # L1 策略
        market_regime=MarketRegime.BULL,
        is_consolidation=False,
        tech_edge_score=75.0,
        orderflow_edge_score=70.0,
        
        # L2 AI/LLM
        news_bias=1,  # 看多
        news_strength=0.8,
        cycle_regime=CycleRegime.POST_HALVING_PRICE_DISCOVERY,
        p_win=0.78,
        
        # L3 風控
        volatility_percentile=0.3,
        max_leverage=15,
    )
    
    print(context)
    print(f"\nML Features shape: {len(context.to_ml_features())}")
    print(f"First 10 features: {context.to_ml_features()[:10]}")
    
    # 添加新聞因子
    news = NewsFactor(
        direction=Direction.LONG,
        confidence=0.85,
        impact_level=ImpactLevel.HIGH,
        tags=['ETF_flow', 'institutional'],
        source_influence=0.9
    )
    context.set_news_factor(news)
    
    print(f"\n更新後 news_strength: {context.news_strength}")
    print(f"Event risk level: {context.event_risk_level.value}")
