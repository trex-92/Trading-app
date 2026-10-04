"""Event-driven backtest over 1-minute bars with the shared risk layer.

Fill model (deliberately conservative, all assumptions are written down here and in the result notes):
 * A signal is created at the CLOSE of the trigger bar; the order is a marketable limit that is live for the NEXT
   1m bar only: fill at that bar's open if open <= limit, else at the limit if the bar trades down to it, else no fill.
 * Exits are checked on 1m bars. If one bar touches both the stop and a target, the STOP is assumed to hit first.
 * A gap through the stop fills at the bar open (worse than the stop). Targets are limit orders: fill at
   max(target, open). After T1 the stop moves to breakeven and is first checked on the NEXT bar.
 * Costs: `cost_per_share_round_trip` is charged on every share. Spread/quote filters need bid/ask history, which bar
   data does not have, so they are NOT applied in backtests.
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime

from .bars import Bar, is_premarket, is_regular, tod
from .calendar import Calendar
from .context import DayContext, TickerState
from .params import SharedParams
from .risk import DayRisk, DrawdownGuard, size_position
from .stats import summarize
from .strategies import Signal, Strategy


@dataclass
class Position:
    sig: Signal
    entry_ts: datetime
    entry: float
    shares: int
    stop: float
    sizing: dict
    remaining: int = 0
    t1_done: bool = False
    t1_bar: datetime | None = None
    exits: list = field(default_factory=list)

    def __post_init__(self):
        self.remaining = self.shares


@dataclass
class Result:
    trades: list
    stats: dict
    equity_curve: list
    notes: list
    skipped: dict


class Backtester:
    def __init__(self, strategies: list[Strategy], shared: SharedParams, budget: float,
                 calendar: Calendar | None = None, warmup_days: int = 1):
        self.strategies, self.shared = strategies, shared.validate()
        self.budget, self.calendar, self.warmup_days = budget, calendar or Calendar(), warmup_days

    # ---------------------------------------------------------------------------------------------
    def run(self, data: dict[str, list[Bar]]) -> Result:
        universe = list(data)
        by_day: dict[str, dict[date, dict]] = {}
        for t, bars in data.items():
            d: dict[date, dict] = {}
            for b in sorted(bars, key=lambda x: x.ts):
                slot = d.setdefault(b.ts.date(), {"pm": [], "reg": {}})
                if is_regular(b.ts):
                    slot["reg"][b.ts] = b
                elif is_premarket(b.ts):
                    slot["pm"].append(b)
            by_day[t] = d
        days = sorted({day for t in universe for day, s in by_day[t].items() if s["reg"]})
        states = {t: TickerState() for t in universe}
        prior: dict[str, dict | None] = {t: None for t in universe}
        self.equity, self.trades, self.curve = self.budget, [], []
        self.guard, self.breaker_events = DrawdownGuard(self.shared), []
        self.skipped: dict[str, int] = {}
        for di, day in enumerate(days):
            self.day = day
            tradable = di >= self.warmup_days and self.calendar.blocked(day) is None
            if di >= self.warmup_days and not tradable:
                self.skipped[self.calendar.blocked(day)] = self.skipped.get(self.calendar.blocked(day), 0) + 1
            self.not_before = self.calendar.no_entries_before(day)
            self.risk = DayRisk(self.equity)
            self.pos: Position | None = None
            self.pending: Signal | None = None
            self.lock: dict[str, str] = {}
            ctxs = {}
            for t in universe:
                if by_day[t].get(day, {}).get("reg"):
                    c = ctxs[t] = DayContext(t, day, states[t], prior[t])
                    for b in by_day[t][day]["pm"]:
                        c.add_premarket(b)
            timeline = sorted({ts for t in ctxs for ts in by_day[t][day]["reg"]})
            for ts in timeline:
                for t in universe:
                    c = ctxs.get(t)
                    bar = by_day[t][day]["reg"].get(ts) if c else None
                    if bar is not None:
                        self._step(t, c, bar, c.add_1m(bar), tradable)
            for t, c in ctxs.items():  # data ended with a position open: close at the last price
                if self.pos and self.pos.sig.ticker == t:
                    self._exit(self.pos.remaining, c.bars1[-1].close, c.now, "end_of_data")
                if c.bars1:
                    prior[t] = {"high": c.hod, "low": c.lod, "close": c.bars1[-1].close}
        notes = self._notes(days)
        return Result(self.trades, summarize(self.trades, self.budget), self.curve, notes, self.skipped)

    def _notes(self, days) -> list[str]:
        notes = ["Untested starting defaults; a backtest is not a forecast. Paper trade 100 trades per strategy before any live money.",
                 "Fills: next-bar marketable limit; stop assumed first when a bar touches stop and target; spread filter not applied (no bid/ask in bars).",
                 f"Costs assumed: ${self.shared.cost_per_share_round_trip:.3f}/share round trip."]
        if not self.calendar.configured:
            notes.append("WARNING: no event calendar configured. FOMC, CPI/NFP and half days were NOT excluded.")
        if len(days) <= self.warmup_days:
            notes.append("Not enough days: the first day is used for indicator warm-up and prior-day levels.")
        for day, level, dd in self.breaker_events:
            notes.append(f"Drawdown breaker {level} tripped on {day} at {dd}% from peak: " + (
                f"risk per trade cut to {self.shared.breaker1_risk_pct}% until a new equity peak." if level == 1 else
                "live trading would be disabled and the account returned to paper, so no further trades are simulated."))
        if len(self.trades) < 100:
            notes.append(f"Only {len(self.trades)} trades: too few for the averages to mean much.")
        return notes

    # ---------------------------------------------------------------------------------------------
    def _step(self, ticker, ctx, bar, new5, tradable):
        if self.pending and self.pending.ticker == ticker:
            self._try_fill(bar, ctx)
        if self.pos and self.pos.sig.ticker == ticker:
            self._manage(bar, ctx, new5)
        if not tradable:
            return
        sigs = [s for st in self.strategies if self.lock.get(ticker, st.family) == st.family
                for s in [st.on_bar(ctx, new5)] if s]
        if self.pos or self.pending or not sigs or self.guard.live_disabled:
            return
        sig = sigs[0]
        if self.risk.can_enter(self.shared):
            return
        if self.not_before and tod(sig.ts) < self.not_before:
            return
        sizing = size_position(self.equity, sig.entry, sig.stop, self.shared, self.guard.risk_pct)
        if sizing["skip"]:
            self.skipped[sizing["skip"].split(" ")[0]] = self.skipped.get(sizing["skip"].split(" ")[0], 0) + 1
            return
        sig.regime = {**sig.regime, "sizing": sizing}
        self.pending = sig

    def _try_fill(self, bar, ctx):
        sig, self.pending = self.pending, None
        if bar.open <= sig.entry:
            px = bar.open
        elif bar.low <= sig.entry:
            px = sig.entry
        else:
            return  # price ran away: order expires unfilled
        sizing = sig.regime["sizing"]
        self.pos = Position(sig, bar.ts, px, sizing["shares"], sig.stop, sizing)
        self.risk.trades += 1
        self.lock[sig.ticker] = sig.family

    def _manage(self, bar, ctx, new5):
        pos = self.pos
        if bar.open <= pos.stop:
            self._exit(pos.remaining, bar.open, ctx.now, "stop_gap")
            return
        if bar.low <= pos.stop:
            self._exit(pos.remaining, pos.stop, ctx.now, "breakeven_stop" if pos.t1_done else "stop")
            return
        if not pos.t1_done and bar.high >= pos.sig.t1:
            qty = pos.remaining if pos.shares < 2 else int(math.floor(pos.shares * pos.sig.t1_frac))
            self._exit(qty, max(pos.sig.t1, bar.open), ctx.now, "target1")
            if self.pos is None:
                return
            pos.t1_done, pos.t1_bar, pos.stop = True, bar.ts, pos.entry
        if pos.t1_done and pos.sig.t2 and bar.high >= pos.sig.t2:
            self._exit(pos.remaining, max(pos.sig.t2, bar.open), ctx.now, "target2")
            return
        strat = next(s for s in self.strategies if s.name == pos.sig.strategy)
        reason = strat.discretionary_exit(pos, ctx, new5)
        mins = (ctx.now - pos.entry_ts).total_seconds() / 60
        if not reason and pos.sig.time_stop_minutes and mins >= pos.sig.time_stop_minutes \
                and (pos.sig.time_stop_always or not pos.t1_done):
            reason = "time_stop"
        if not reason and tod(ctx.now) >= pos.sig.flat:
            reason = "flat_eod"
        if reason:
            self._exit(pos.remaining, bar.close, ctx.now, reason)

    def _exit(self, qty, price, ts, reason):
        pos = self.pos
        qty = min(qty, pos.remaining)
        pos.exits.append({"ts": ts.isoformat(), "qty": qty, "price": round(price, 4), "reason": reason})
        pos.remaining -= qty
        if pos.remaining > 0:
            return
        sig = pos.sig
        gross = sum((e["price"] - pos.entry) * e["qty"] for e in pos.exits)
        costs = self.shared.cost_per_share_round_trip * pos.shares
        net = gross - costs
        r = net / (pos.shares * (sig.entry - sig.stop))
        self.equity += net
        self.risk.record(net)
        before = self.guard.level
        if self.guard.record(net, self.budget) > before:
            self.breaker_events.append((self.day.isoformat(), self.guard.level, round(self.guard.drawdown_pct(self.budget), 2)))
        self.trades.append({
            "date": self.day.isoformat(), "ticker": sig.ticker, "strategy": sig.strategy,
            "entry_ts": pos.entry_ts.isoformat(), "entry": round(pos.entry, 4), "planned_entry": sig.entry,
            "stop": sig.stop, "t1": round(sig.t1, 4), "t2": sig.t2 and round(sig.t2, 4), "shares": pos.shares,
            "exits": pos.exits, "costs": round(costs, 2), "pnl": round(net, 2), "r": round(r, 3),
            "minutes_held": round((ts - pos.entry_ts).total_seconds() / 60, 1), "regime": sig.regime})
        self.curve.append({"ts": ts.isoformat(), "equity": round(self.equity, 2)})
        self.pos = None
