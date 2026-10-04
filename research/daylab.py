"""Day-level research lab: one `Day` object per symbol-day with 1-minute arrays (regular session) and daily context
(prior day, gap, premarket, trailing volume and range), an engine-faithful trade simulator, and `Replay`, a strategy that pushes
event-level signals through the repo's real Backtester so any formula can be judged with the engine's portfolio rules.

No look-ahead: every array value at minute i is what the engine's DayContext knows after bar i has closed.
Trailing statistics only use earlier days."""
import math
from array import array
from dataclasses import dataclass, field

from bot.intraday.context import DayContext, TickerState
from bot.intraday.strategies import Signal, Strategy

from .common import *                                                      # noqa: F401,F403

NAN = float("nan")
FULL_DAY = 385            # a regular US session has 390 one-minute bars; shorter days (half days) are skipped
FLAT = 385                # engine default: close anything still open at 15:55 (trading minute 385)


class Day:
    __slots__ = ("t", "day", "n", "o", "h", "l", "c", "v", "vwap", "ema9", "ema20", "atr1", "atr5", "hod", "lod", "cumv", "new5", "c5",
                 "prior_close", "prior_high", "prior_low", "gap_pct", "pm_high", "pm_low", "pm_vol", "pm_last", "atr_d", "avg_pm_vol",
                 "prev_ret1", "prev_ret5", "hi20", "lo20", "prev_cumv", "full")

    def ret_open(self, i):               # % return of the close of minute i versus the day's open
        return (self.c[i] / self.o[0] - 1) * 100

    def or_high(self, k):                # high of the first k minutes (k <= i + 1)
        return max(self.h[:k])

    def or_low(self, k):
        return min(self.l[:k])

    def rvol(self, i):                   # cumulative volume so far / the trailing average at the same minute
        pcv = [a[i] for a in self.prev_cumv if len(a) > i]
        if len(pcv) < 5:
            return NAN
        avg = sum(pcv) / len(pcv)
        return self.cumv[i] / avg if avg > 0 else NAN


def _mean(xs):
    return sum(xs) / len(xs) if xs else NAN


def build_days(data: dict, tickers: list[str]) -> dict:
    """{(ticker, day): Day}. Needs regular + premarket bars (the cache has 04:00-20:00)."""
    out = {}
    for t in tickers:
        state, prior = TickerState(), None
        byday = defaultdict(list)
        for b in data[t]:
            byday[b.ts.astimezone(US.tz).date()].append(b)
        hist = []                                           # earlier full days: dict(open, high, low, close, tr, pm_vol, cumv)
        for day in sorted(byday):
            reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
            pm = [b for b in byday[day] if US.is_pre(b.ts)]
            if not reg:
                continue
            ctx = DayContext(t, day, state, prior, US)
            for b in pm:
                ctx.add_premarket(b)
            d = Day()
            d.t, d.day, d.n = t, day, len(reg)
            for name in ("o", "h", "l", "c", "v", "vwap", "ema9", "ema20", "atr1", "atr5", "hod", "lod", "cumv", "new5", "c5"):
                setattr(d, name, array("d"))
            cum = 0.0
            for b in reg:
                new5 = ctx.add_1m(b)
                cum += b.volume
                d.o.append(b.open); d.h.append(b.high); d.l.append(b.low); d.c.append(b.close); d.v.append(b.volume)
                d.vwap.append(ctx.vwap); d.hod.append(ctx.hod); d.lod.append(ctx.lod); d.cumv.append(cum)
                d.ema9.append(ctx.ema9 if ctx.ema9 is not None else NAN); d.ema20.append(ctx.ema20 if ctx.ema20 is not None else NAN)
                d.atr1.append(ctx.atr1 if ctx.atr1 is not None else NAN); d.atr5.append(ctx.atr5 if ctx.atr5 is not None else NAN)
                d.new5.append(1.0 if new5 else 0.0); d.c5.append(ctx.bars5[-1].close if ctx.bars5 else NAN)
            d.full = len(reg) >= FULL_DAY
            d.prior_close = prior["close"] if prior else None
            d.prior_high = prior["high"] if prior else None
            d.prior_low = prior["low"] if prior else None
            d.gap_pct = (d.o[0] / d.prior_close - 1) * 100 if d.prior_close else NAN
            d.pm_high, d.pm_low = ctx.pm_high, ctx.pm_low
            d.pm_vol = sum(b.volume for b in pm)
            d.pm_last = pm[-1].close if pm else None
            h14, h20 = hist[-14:], hist[-20:]
            d.atr_d = _mean([x["tr"] for x in h14]) if len(h14) >= 5 else NAN
            d.avg_pm_vol = _mean([x["pm_vol"] for x in h20]) if len(h20) >= 5 else NAN
            d.prev_ret1 = (hist[-1]["close"] / hist[-2]["close"] - 1) * 100 if len(hist) >= 2 else NAN
            d.prev_ret5 = (hist[-1]["close"] / hist[-6]["close"] - 1) * 100 if len(hist) >= 6 else NAN
            d.hi20 = max(x["high"] for x in h20) if len(h20) >= 5 else NAN
            d.lo20 = min(x["low"] for x in h20) if len(h20) >= 5 else NAN
            d.prev_cumv = [x["cumv"] for x in h20]
            out[(t, day)] = d
            if d.full:
                pc = hist[-1]["close"] if hist else None
                hi, lo = ctx.hod, ctx.lod
                tr = max(hi - lo, abs(hi - pc), abs(lo - pc)) if pc else hi - lo
                hist.append({"open": d.o[0], "high": hi, "low": lo, "close": d.c[-1], "tr": tr, "pm_vol": d.pm_vol, "cumv": d.cumv})
            prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return out


# ------------------------------------------------------------------------------------------------------------------------
@dataclass
class Ev:
    """A candidate trade: the signal is made at the CLOSE of minute index j; the order is a limit at E for the next bar only."""
    t: str
    day: date
    j: int
    E: float
    S: float
    T1: float | None = None          # first target price (None = none)
    T2: float | None = None          # second target price (only active after T1)
    kw: dict = field(default_factory=dict)   # exit settings: t1_frac, ts_min, ts_always, flat_min, exit_mode
    f: dict = field(default_factory=dict)    # features / tags for later analysis


def simulate(d: Day, e: Ev, cost_ps: float = 0.02, fee_rule: bool = True, fill: str = "limit", slip: float = 0.01):
    """Replicates Backtester._try_fill / _manage for one trade (no portfolio limits). Returns a result dict, or {"skip": reason}."""
    k = e.kw
    E, S = e.E, e.S
    R = E - S
    i0 = e.j + 1
    if i0 >= d.n or R <= 0:
        return {"skip": "invalid"}
    if fee_rule and cost_ps > 0.10 * R:
        return {"skip": "cost"}
    o, l = d.o[i0], d.l[i0]
    if fill == "market":
        px = o + slip
    elif o <= E:
        px = o
    elif l <= E:
        px = E
    else:
        return {"skip": "nofill"}
    t1_frac, ts_min, ts_always = k.get("t1_frac", 0.5), k.get("ts_min"), k.get("ts_always", False)
    flat_min, mode = k.get("flat_min", FLAT), k.get("exit_mode")
    stop, rem, t1_done, pnl, reason, held = S, 1.0, False, 0.0, None, 0
    mfe = mae = 0.0
    for i in range(i0, d.n):
        o, h, l, c = d.o[i], d.h[i], d.l[i], d.c[i]
        elapsed = i + 1
        held = elapsed - i0
        mfe = max(mfe, (h - px) / R)
        mae = min(mae, (l - px) / R)
        if o <= stop:
            pnl += rem * (o - px); rem = 0.0; reason = "stop_gap"; break
        if l <= stop:
            pnl += rem * (stop - px); rem = 0.0; reason = "breakeven_stop" if t1_done else "stop"; break
        if not t1_done and e.T1 is not None and h >= e.T1:
            pnl += t1_frac * (max(e.T1, o) - px); rem -= t1_frac; t1_done = True
            if rem <= 1e-9:
                rem = 0.0; reason = "target1"; break
            stop = px
        if t1_done and e.T2 is not None and h >= e.T2:
            pnl += rem * (max(e.T2, o) - px); rem = 0.0; reason = "target2"; break
        r_ = None
        if mode and d.new5[i]:
            c5 = d.c5[i]
            if mode == "ema20" and d.ema20[i] == d.ema20[i] and c5 < d.ema20[i]:
                r_ = "ema20"
            elif mode == "ema9" and d.ema9[i] == d.ema9[i] and c5 < d.ema9[i]:
                r_ = "ema9"
            elif mode == "vwap" and c5 < d.vwap[i]:
                r_ = "vwap"
        if not r_ and ts_min and held >= ts_min and (ts_always or not t1_done):
            r_ = "time_stop"
        if not r_ and elapsed >= flat_min:
            r_ = "flat_eod"
        if r_:
            pnl += rem * (c - px); rem = 0.0; reason = r_; break
    if rem > 0:
        pnl += rem * (d.c[d.n - 1] - px); reason = "end_of_data"
    net = pnl - cost_ps
    stop_pct = R / E * 100
    ret = net / px * 100
    return {"r": net / R, "ret_pct": ret, "acct_ret": ret * min(1.0, 1.0 / stop_pct), "reason": reason, "held": held, "fill": px,
            "win": net > 0, "t1": t1_done, "mfe": mfe, "mae": mae, "stop_pct": stop_pct}


def run_events(days: dict, evs: list, **kw) -> list:
    """Simulate every event; returns records usable with scalp_lab.agg / boot (keys day, r, ret_pct, acct_ret) plus the event."""
    out = []
    for e in evs:
        d = days.get((e.t, e.day))
        if d is None:
            continue
        s = simulate(d, e, **kw)
        if "skip" in s:
            continue
        out.append({**s, "day": e.day, "t": e.t, "ticker": e.t, "ev": e, "f": e.f})
    return out


def count_skips(days: dict, evs: list, **kw) -> Counter:
    c = Counter()
    for e in evs:
        d = days.get((e.t, e.day))
        if d is not None:
            c[simulate(d, e, **kw).get("skip", "filled")] += 1
    return c


# ------------------------------------------------------------------------------------------------------------------------
class Replay(Strategy):
    """Emits pre-computed Ev signals at the right minute, so the engine applies its real fills, exits and portfolio limits."""
    name, family = "R", "trend"

    def __init__(self, evs):
        self.by = defaultdict(list)
        for e in evs:
            self.by[(e.t, e.day)].append(e)

    def on_bar(self, ctx, new5):
        for e in self.by.get((ctx.ticker, ctx.day), ()):
            if ctx.elapsed == e.j + 1:
                k = e.kw
                t1 = e.T1 if e.T1 is not None else e.E * 10.0          # no first target: never reached
                return Signal("R", ctx.ticker, ctx.now, e.E, e.S, t1, e.T2, {"exit_mode": k.get("exit_mode")}, t1_frac=k.get("t1_frac", 0.5),
                              time_stop_minutes=k.get("ts_min"), time_stop_always=k.get("ts_always", False),
                              flat_min=k.get("flat_min", FLAT), family="trend", elapsed=ctx.elapsed)
        return None

    def discretionary_exit(self, pos, ctx, new5):
        mode = pos.sig.regime.get("exit_mode")
        if not mode or not new5 or not ctx.bars5:
            return None
        c5 = ctx.bars5[-1].close
        if mode == "ema20" and ctx.ema20 is not None and c5 < ctx.ema20:
            return "ema20"
        if mode == "ema9" and ctx.ema9 is not None and c5 < ctx.ema9:
            return "ema9"
        if mode == "vwap" and c5 < ctx.vwap:
            return "vwap"
        return None


def engine_replay(evs, tickers, data, shared=None, budget=100000.0):
    return engine(lambda: Replay(evs), tickers, data, budget=budget, shared=shared)


RELAX = {"max_trades_per_day": 99, "stop_after_consecutive_losses": 99, "daily_max_loss_pct": 100.0, "max_cost_pct_of_1R": 1e9}
