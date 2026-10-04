"""Shared entry point for backtests (used by the CLI and by the worker that serves app requests)."""
from dataclasses import asdict
from datetime import time

from .backtest import Backtester
from .bars import Bar, is_regular
from .calendar import Calendar
from .params import RangeParams, ScalpParams, SharedParams, TrendParams, apply_overrides
from .strategies import Range, Scalp, Trend

NAMES = {"A": "Scalp (VWAP pullback)", "B": "Trend following", "C": "Range trading"}
MAX_TRADES_RETURNED = 500


def _jsonable(d: dict) -> dict:
    return {k: (v.strftime("%H:%M") if isinstance(v, time) else v) for k, v in d.items()}


def defaults() -> dict:
    return {"shared": _jsonable(asdict(SharedParams())), "A": _jsonable(asdict(ScalpParams())),
            "B": _jsonable(asdict(TrendParams())), "C": _jsonable(asdict(RangeParams()))}


def build(codes: list[str], overrides: dict | None):
    """overrides = {"shared": {...}, "A": {...}, "B": {...}, "C": {...}}; unknown keys raise ValueError."""
    overrides = overrides or {}
    bad = set(overrides) - {"shared", "A", "B", "C"}
    if bad:
        raise ValueError(f"unknown override section(s) {sorted(bad)}")
    shared = apply_overrides(SharedParams(), overrides.get("shared")).validate()
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
                 calendar: Calendar | None = None) -> dict:
    if budget <= 0:
        raise ValueError("budget must be positive")
    strategies, shared = build(codes, overrides)
    res = Backtester(strategies, shared, budget, calendar).run(data)
    coverage = {}
    for t, bars in data.items():
        reg = [b for b in bars if is_regular(b.ts)]
        coverage[t] = {"bars": len(bars), "days": len({b.ts.date() for b in reg}),
                       "first": reg[0].ts.isoformat() if reg else None, "last": reg[-1].ts.isoformat() if reg else None,
                       "has_premarket": any(not is_regular(b.ts) for b in bars)}
    return {"strategies": codes, "budget": budget, "stats": res.stats, "notes": res.notes, "skipped": res.skipped,
            "equity_curve": res.equity_curve[-MAX_TRADES_RETURNED:], "trades": res.trades[-MAX_TRADES_RETURNED:],
            "trades_total": len(res.trades), "data": coverage}
