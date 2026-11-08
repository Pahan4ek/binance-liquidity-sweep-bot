from dataclasses import dataclass, field


@dataclass
class Candle:
    open_time: int         # Milliseconds UTC
    close_time: int        # Milliseconds UTC
    open: float
    high: float
    low: float
    close: float
    volume: float          # Base asset volume
    taker_buy_vol: float   # Base asset taker buy volume
    is_closed: bool = True
    oi_open: float | None = None
    oi_close: float | None = None

    @property
    def taker_sell_vol(self) -> float:
        return max(0.0, self.volume - self.taker_buy_vol)

    @property
    def delta(self) -> float:
        return self.taker_buy_vol - self.taker_sell_vol

    @property
    def delta_ratio(self) -> float:
        return (abs(self.delta) / self.volume) if self.volume > 0 else 0.0

    @property
    def oi_delta(self) -> float | None:
        if self.oi_open is not None and self.oi_close is not None:
            return self.oi_close - self.oi_open
        return None

@dataclass
class SwingLevel:
    time: int
    price: float
    level_type: str  # "high" or "low"
    bar_index: int = 0
    active: bool = True
    mitigated_at: int | None = None

@dataclass
class SweepSignal:
    direction: str          # "short" or "long"
    time: int               # Milliseconds UTC
    candle_close: float
    level_price: float
    extreme_price: float    # Candle high for short, low for long
    stop_loss: float
    score: int              # Out of 6
    reasons: list[str] = field(default_factory=list)
    delta: float = 0.0
    delta_ratio: float = 0.0
    volume_ratio: float = 0.0
    oi_delta: float | None = None
