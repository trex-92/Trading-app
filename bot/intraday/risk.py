"""The shared risk layer: position sizing and per-day limits, applied across all strategies."""
import math
from dataclasses import dataclass

from .params import SharedParams


def cost_per_share(entry: float, p: SharedParams) -> float:
    """All-in round-trip cost per share: fixed per-share part plus a percentage of the price."""
    return p.cost_per_share_round_trip + entry * p.cost_pct_round_trip / 100


def size_position(equity: float, entry: float, stop: float, p: SharedParams, risk_pct: float | None = None) -> dict:
    """shares = floor(min(equity*risk%/risk_per_share, equity*max_notional%/entry)); skip rules per the spec."""
    rps = entry - stop
    out = {"equity": round(equity, 2), "entry": entry, "stop": stop, "risk_per_share": round(rps, 4)}
    if rps <= 0 or entry <= 0 or equity <= 0:
        return {**out, "shares": 0, "skip": "invalid entry/stop/equity"}
    risk_pct = p.risk_per_trade_pct if risk_pct is None else risk_pct
    out["risk_pct"] = risk_pct
    by_risk = equity * risk_pct / 100 / rps
    by_notional = equity * p.max_notional_pct / 100 / entry
    lot = p.lot_size
    shares = math.floor(min(by_risk, by_notional) / lot) * lot   # whole board lots only
    out.update(shares_by_risk=round(by_risk, 2), shares_by_notional=round(by_notional, 2), shares=shares,
               binding="notional" if by_notional < by_risk else "risk")
    if shares < 1:
        return {**out, "skip": "shares < 1"}
    cost_ps = cost_per_share(entry, p)
    cost = shares * cost_ps
    out["est_round_trip_cost"] = round(cost, 2)
    if cost_ps > p.max_cost_pct_of_1R / 100 * rps:
        return {**out, "skip": f"cost {cost_ps:.4f}/share = {cost_ps / rps * 100:.0f}% of 1R (max {p.max_cost_pct_of_1R}%)"}
    return {**out, "skip": None}


@dataclass
class DayRisk:
    start_equity: float
    realized: float = 0.0
    trades: int = 0
    consecutive_losses: int = 0

    def can_enter(self, p: SharedParams) -> str | None:
        if self.realized <= -p.daily_max_loss_pct / 100 * self.start_equity:
            return "daily loss limit reached"
        if self.trades >= p.max_trades_per_day:
            return "max trades per day reached"
        if self.consecutive_losses >= p.stop_after_consecutive_losses:
            return "consecutive-loss stop"
        return None

    def record(self, net_pnl: float) -> None:
        self.realized += net_pnl
        self.consecutive_losses = self.consecutive_losses + 1 if net_pnl < 0 else 0


class DrawdownGuard:
    """Tracks realized P&L against its peak and applies the spec's two breakers.

    Level 0: normal. Level 1 (drawdown >= breaker1_pct): risk per trade falls to breaker1_risk_pct.
    Level 2 (>= breaker2_pct): live trading disabled, back to paper. INTERPRETATION: a tripped level is cleared only
    when realized P&L makes a new peak (the spec does not say how to step back up).
    Drawdown % is relative to (base capital + peak P&L), where base is the capital currently allocated."""

    def __init__(self, p: SharedParams, cum: float = 0.0, peak: float = 0.0, level: int = 0):
        self.p, self.cum, self.peak, self.level = p, cum, max(peak, cum), level

    def drawdown_pct(self, base: float) -> float:
        cap_peak = base + self.peak
        return 0.0 if cap_peak <= 0 else (self.peak - self.cum) / cap_peak * 100

    def record(self, net_pnl: float, base: float) -> int:
        """Apply a closed trade; returns the new level."""
        self.cum += net_pnl
        if self.cum >= self.peak:
            self.peak, self.level = self.cum, 0
        else:
            dd = self.drawdown_pct(base)
            if dd >= self.p.breaker2_pct:
                self.level = 2
            elif dd >= self.p.breaker1_pct:
                self.level = max(self.level, 1)
        return self.level

    @property
    def risk_pct(self) -> float:
        return self.p.breaker1_risk_pct if self.level >= 1 else self.p.risk_per_trade_pct

    @property
    def live_disabled(self) -> bool:
        return self.level >= 2

    def dump(self) -> dict:
        return {"cum": self.cum, "peak": self.peak, "level": self.level}
