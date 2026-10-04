"""Every numeric threshold lives here as an UNTESTED STARTING DEFAULT. Override per run; unknown keys are rejected."""
from dataclasses import dataclass, fields, replace
from datetime import time


def _coerce(default, v):
    if isinstance(default, time):
        return v if isinstance(v, time) else time.fromisoformat(str(v))
    if isinstance(default, bool):
        return bool(v)
    return type(default)(v)


def apply_overrides(obj, overrides: dict | None):
    overrides = overrides or {}
    current = {f.name: getattr(obj, f.name) for f in fields(obj)}
    bad = sorted(set(overrides) - set(current))
    if bad:
        raise ValueError(f"unknown parameter(s) {bad}; allowed: {sorted(current)}")
    return replace(obj, **{k: _coerce(current[k], v) for k, v in overrides.items()})


@dataclass(frozen=True)
class SharedParams:
    risk_per_trade_pct: float = 1.0
    risk_hard_ceiling_pct: float = 1.0   # the bot refuses to start if risk_per_trade_pct is above this
    max_notional_pct: float = 100.0
    daily_max_loss_pct: float = 2.0     # two full losses at 1% risk
    max_trades_per_day: int = 3
    stop_after_consecutive_losses: int = 2
    max_open_positions: int = 1
    max_cost_pct_of_1R: float = 10.0
    # ASSUMPTION (not from the spec): all-in round-trip cost per share (commission + half-spread each way).
    cost_per_share_round_trip: float = 0.02
    allow_short: bool = False
    cost_pct_round_trip: float = 0.0     # % of notional (stamp duty, clearing, brokerage); set by the market profile
    lot_size: int = 1                    # shares per board lot; set by the market profile
    max_trade_notional: float = 0.0      # optional cap on the money in ONE trade, in the market currency (0 = no cap)
    allow_fractional: bool = False       # US only: trade fractions of a share (needs broker support; test with the smoke script)
    fractional_step: float = 0.01        # smallest fraction of a share used when allow_fractional is on
    # Drawdown breakers, measured from the peak of the strategy capital (budget + realized P&L).
    breaker1_pct: float = 6.0            # at this drawdown, risk per trade drops to breaker1_risk_pct
    breaker1_risk_pct: float = 0.5
    breaker2_pct: float = 10.0           # at this drawdown, live trading is disabled and we go back to paper

    def validate(self) -> "SharedParams":
        if self.risk_hard_ceiling_pct > 1.0:
            raise ValueError("risk_hard_ceiling_pct may not be above 1.0")
        if self.risk_per_trade_pct > self.risk_hard_ceiling_pct:
            raise ValueError(f"risk_per_trade_pct {self.risk_per_trade_pct} is above the hard ceiling "
                             f"{self.risk_hard_ceiling_pct}; refusing to start")
        if self.lot_size < 1:
            raise ValueError("lot_size must be at least 1")
        if self.max_trade_notional < 0:
            raise ValueError("max_trade_notional cannot be negative")
        if self.allow_fractional and not 0 < self.fractional_step <= 1:
            raise ValueError("fractional_step must be between 0 and 1")
        if not 0 < self.breaker1_risk_pct <= self.risk_per_trade_pct or not 0 < self.breaker1_pct < self.breaker2_pct:
            raise ValueError("breakers need 0 < breaker1_pct < breaker2_pct and 0 < breaker1_risk_pct <= risk_per_trade_pct")
        if self.risk_per_trade_pct <= 0 or self.max_notional_pct <= 0:
            raise ValueError("risk and notional percentages must be positive")
        if self.allow_short:
            raise ValueError("allow_short is not supported (long only)")
        if self.max_open_positions != 1:
            raise ValueError("only max_open_positions=1 is supported")
        return self


# Windows are TRADING MINUTES since the open (US: 15 = 09:45, 30 = 10:00, 60 = 10:30, 120 = 11:30, 150 = 12:00, 300 = 14:30).
@dataclass(frozen=True)
class ScalpParams:  # Strategy A
    window_start_min: int = 15
    window_end_min: int = 120
    max_vwap_crosses: int = 2
    max_pullbacks_per_day: int = 2
    entry_offset: float = 0.02          # in price units; scale for non-US markets with other tick sizes
    stop_buffer_min: float = 0.02
    stop_buffer_atr: float = 0.1
    max_stop_pct: float = 0.25
    t1_r: float = 1.5
    t2_r: float = 2.5
    time_stop_minutes: int = 25
    flat_before_close_min: int = 5     # ASSUMPTION: spec gives A no end-of-day exit (US 15:55)
    pullback_max_age_bars: int = 10    # ASSUMPTION: spec does not say how long a pullback stays valid


@dataclass(frozen=True)
class TrendParams:  # Strategy B
    window_start_min: int = 30
    window_end_min: int = 150
    max_vwap_crosses: int = 1
    crosses_since_min: int = 15
    stop_buffer_atr: float = 0.1
    min_stop_pct: float = 0.3
    max_stop_pct: float = 1.0
    t1_r: float = 2.0
    flat_before_close_min: int = 5


@dataclass(frozen=True)
class RangeParams:  # Strategy C
    window_start_min: int = 60
    window_end_min: int = 300
    min_crosses: int = 3
    min_width_pct: float = 0.3
    max_width_pct: float = 1.0
    max_trades_per_ticker: int = 2
    entry_zone: float = 0.15
    stop_buffer: float = 0.10
    kill_band: float = 0.10
    t1_r: float = 1.5
    t2_min_r: float = 2.0
    t2_inset: float = 0.10
    time_stop_minutes: int = 90
    flat_before_close_min: int = 30
