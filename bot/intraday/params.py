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
    risk_per_trade_pct: float = 0.5
    max_notional_pct: float = 100.0
    daily_max_loss_pct: float = 1.5
    max_trades_per_day: int = 3
    stop_after_consecutive_losses: int = 2
    max_open_positions: int = 1
    max_cost_pct_of_1R: float = 10.0
    # ASSUMPTION (not from the spec): all-in round-trip cost per share (commission + half-spread each way).
    cost_per_share_round_trip: float = 0.02
    allow_short: bool = False

    def validate(self) -> "SharedParams":
        if self.risk_per_trade_pct > 1.0:
            raise ValueError("risk_per_trade_pct must never exceed 1.0")
        if self.risk_per_trade_pct <= 0 or self.max_notional_pct <= 0:
            raise ValueError("risk and notional percentages must be positive")
        if self.allow_short:
            raise ValueError("allow_short is not supported (long only)")
        if self.max_open_positions != 1:
            raise ValueError("only max_open_positions=1 is supported")
        return self


@dataclass(frozen=True)
class ScalpParams:  # Strategy A
    window_start: time = time(9, 45)
    window_end: time = time(11, 30)
    max_vwap_crosses: int = 2
    max_pullbacks_per_day: int = 2
    entry_offset: float = 0.02
    stop_buffer_min: float = 0.02
    stop_buffer_atr: float = 0.1
    max_stop_pct: float = 0.25
    t1_r: float = 1.5
    t2_r: float = 2.5
    time_stop_minutes: int = 25
    flat_time: time = time(15, 55)  # ASSUMPTION: spec gives A no end-of-day exit
    pullback_max_age_bars: int = 10  # ASSUMPTION: spec does not say how long a pullback stays valid


@dataclass(frozen=True)
class TrendParams:  # Strategy B
    window_start: time = time(10, 0)
    window_end: time = time(12, 0)
    max_vwap_crosses: int = 1
    crosses_since: time = time(9, 45)
    stop_buffer_atr: float = 0.1
    min_stop_pct: float = 0.3
    max_stop_pct: float = 0.6
    t1_r: float = 2.0
    flat_time: time = time(15, 55)


@dataclass(frozen=True)
class RangeParams:  # Strategy C
    window_start: time = time(10, 30)
    window_end: time = time(14, 30)
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
    flat_time: time = time(15, 30)
