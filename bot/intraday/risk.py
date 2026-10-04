"""The shared risk layer: position sizing and per-day limits, applied across all strategies."""
import math
from dataclasses import dataclass

from .params import SharedParams


def size_position(equity: float, entry: float, stop: float, p: SharedParams) -> dict:
    """shares = floor(min(equity*risk%/risk_per_share, equity*max_notional%/entry)); skip rules per the spec."""
    rps = entry - stop
    out = {"equity": round(equity, 2), "entry": entry, "stop": stop, "risk_per_share": round(rps, 4)}
    if rps <= 0 or entry <= 0 or equity <= 0:
        return {**out, "shares": 0, "skip": "invalid entry/stop/equity"}
    by_risk = equity * p.risk_per_trade_pct / 100 / rps
    by_notional = equity * p.max_notional_pct / 100 / entry
    shares = math.floor(min(by_risk, by_notional))
    out.update(shares_by_risk=round(by_risk, 2), shares_by_notional=round(by_notional, 2), shares=shares,
               binding="notional" if by_notional < by_risk else "risk")
    if shares < 1:
        return {**out, "skip": "shares < 1"}
    cost = shares * p.cost_per_share_round_trip
    out["est_round_trip_cost"] = round(cost, 2)
    if cost > p.max_cost_pct_of_1R / 100 * shares * rps:
        return {**out, "skip": f"cost {cost:.2f} > {p.max_cost_pct_of_1R}% of 1R"}
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
