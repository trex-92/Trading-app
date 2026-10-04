"""Shared entry point for backtests (used by the CLI and by the worker that serves app requests)."""
from dataclasses import asdict, replace
from datetime import time

from .backtest import Backtester
from .bars import Bar
from .calendar import Calendar
from .market import US, Market
from .params import RangeParams, ScalpParams, SharedParams, TrendParams, apply_overrides
from .strategies import Range, Scalp, Trend

NAMES = {"A": "Scalp (VWAP pullback)", "B": "Trend following", "C": "Range trading"}
MAX_TRADES_RETURNED = 500


def _jsonable(d: dict) -> dict:
    return {k: (v.strftime("%H:%M") if isinstance(v, time) else v) for k, v in d.items()}


def defaults() -> dict:
    return {"shared": _jsonable(asdict(SharedParams())), "A": _jsonable(asdict(ScalpParams())),
            "B": _jsonable(asdict(TrendParams())), "C": _jsonable(asdict(RangeParams()))}


def base_shared(market: Market = US) -> SharedParams:
    """Shared risk parameters with the market's lot size and (placeholder) cost model applied."""
    return replace(SharedParams(), cost_per_share_round_trip=market.cost_per_share_round_trip,
                   cost_pct_round_trip=market.cost_pct_round_trip, lot_size=market.lot_size)


def build(codes: list[str], overrides: dict | None, market: Market = US):
    """overrides = {"shared": {...}, "A": {...}, "B": {...}, "C": {...}}; unknown keys raise ValueError."""
    overrides = overrides or {}
    bad = set(overrides) - {"shared", "A", "B", "C"}
    if bad:
        raise ValueError(f"unknown override section(s) {sorted(bad)}")
    shared = apply_overrides(base_shared(market), overrides.get("shared")).validate()
    strategies = []
    for c in codes:
        if c == "A":
            strategies.append(Scalp(apply_overrides(ScalpParams(), overrides.get("A"))))
        elif c == "B":
            strategies.append(Trend(apply_overrides(TrendParams(), overrides.get("B"))))
        elif c == "C":
            strategies.append(Range(apply_overrides(RangeParams(), overrides.get("C"))))
        else:
            raise ValueError(f"unknown strategy {c!r}")
    return strategies, shared


def run_backtest(codes: list[str], data: dict[str, list[Bar]], budget: float, overrides: dict | None = None,
                 calendar: Calendar | None = None, market: Market = US) -> dict:
    if budget <= 0:
        raise ValueError("budget must be positive")
    strategies, shared = build(codes, overrides, market)
    res = Backtester(strategies, shared, budget, calendar, market=market).run(data)
    coverage = {}
    for t, bars in data.items():
        reg = [b for b in bars if market.is_regular(b.ts)]
        coverage[t] = {"bars": len(bars), "days": len({b.ts.date() for b in reg}),
                       "first": reg[0].ts.isoformat() if reg else None, "last": reg[-1].ts.isoformat() if reg else None,
                       "has_premarket": any(market.is_pre(b.ts) for b in bars)}
    return {"strategies": codes, "market": market.code, "currency": market.currency, "budget": budget, "stats": res.stats, "notes": res.notes, "skipped": res.skipped,
            "equity_curve": res.equity_curve[-MAX_TRADES_RETURNED:], "trades": res.trades[-MAX_TRADES_RETURNED:],
            "trades_total": len(res.trades), "data": coverage}
