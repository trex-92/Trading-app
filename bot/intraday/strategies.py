"""The three intraday strategies. They only emit Signals; sizing, fills, stops and the shared risk limits
belong to the engine. All times are the CLOSE time of the bar being evaluated (America/New_York).

Spec ambiguities I resolved are marked `# INTERPRETATION`; they are listed in docs/strategies-review.md."""
from dataclasses import dataclass
from datetime import datetime

from .bars import Bar
from .context import DayContext, IB_MIN
from .params import RangeParams, ScalpParams, TrendParams


@dataclass
class Signal:
    strategy: str
    ticker: str
    ts: datetime          # close time of the trigger bar
    entry: float          # planned entry = limit price
    stop: float
    t1: float
    t2: float | None
    regime: dict
    t1_frac: float = 0.5
    time_stop_minutes: int | None = None
    time_stop_always: bool = False  # False = only until T1 is hit
    flat_min: int = 385            # trading minute at/after which any remainder is closed
    family: str = "trend"
    elapsed: int = 0               # trading minutes elapsed when the signal was made


class Strategy:
    name = "?"
    family = "trend"

    def on_bar(self, ctx: DayContext, new5: list[Bar]) -> Signal | None:
        raise NotImplementedError

    def discretionary_exit(self, pos, ctx: DayContext, new5: list[Bar]) -> str | None:
        return None

    def _st(self, ctx: DayContext) -> dict:
        return ctx.scratch.setdefault(self.name, {})

    # Funnel: how many times each stage was reached or where a setup was lost (shown in the backtest results).
    def count(self, key: str, n: int = 1) -> None:
        f = self.__dict__.setdefault("_funnel", {})
        f[key] = f.get(key, 0) + n

    def count_once(self, ctx: DayContext, key: str) -> None:
        """Count at most once per ticker per day."""
        done = self._st(ctx).setdefault("_once", set())
        if (key, ctx.ticker) not in done:
            done.add((key, ctx.ticker))
            self.count(key)

    @property
    def funnel(self) -> dict:
        return dict(self.__dict__.get("_funnel", {}))


# --------------------------------------------------------------------------------------------------------
class Scalp(Strategy):
    name, family = "A", "trend"

    def __init__(self, p: ScalpParams | None = None):
        self.p = p or ScalpParams()

    def _checks(self, ctx: DayContext) -> list:
        """The regime as ordered (name, passed) conditions, so a backtest can say which one removed a setup."""
        if (len(ctx.bars5) < 2 or ctx.ema9 is None or ctx.ema20 is None or not ctx.or_ready
                or ctx.ema9_h[-2] is None or ctx.ema20_h[-2] is None):
            return [("warming_up", False)]
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return [("close_above_vwap", b5.close > vw), ("vwap_rising", ctx.vwap_rising()),
                ("ema9_above_ema20", ctx.ema9 > ctx.ema20),
                ("emas_rising", ctx.ema9 > ctx.ema9_h[-2] and ctx.ema20 > ctx.ema20_h[-2]),
                ("vwap_crosses_ok", ctx.vwap_crosses() <= self.p.max_vwap_crosses),
                ("broke_opening_range", any(b.close > ctx.or_high for b in ctx.bars5))]

    def regime(self, ctx: DayContext) -> dict | None:
        if not all(ok for _, ok in self._checks(ctx)):
            return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return {"close5": b5.close, "vwap": round(vw, 4), "ema9": round(ctx.ema9, 4), "ema20": round(ctx.ema20, 4),
                "vwap_crosses": ctx.vwap_crosses(), "or_high": ctx.or_high}

    def on_bar(self, ctx, new5):
        p = self.p
        st = self._st(ctx)
        if not (p.window_start_min <= ctx.elapsed <= p.window_end_min):
            return None
        self.count("window_minutes")
        self.count_once(ctx, "days_evaluated")
        failed = next((name for name, ok in self._checks(ctx) if not ok), None)
        if failed:
            self.count(f"regime_failed_{failed}")
            st["cur"] = None
            return None
        reg = self.regime(ctx)
        self.count("regime_minutes")
        self.count_once(ctx, "days_regime_on")
        bar, i = ctx.bars1[-1], len(ctx.bars1) - 1
        prev = ctx.bars1[-2] if i >= 1 else None
        last3 = ctx.bars1[-3:]
        vol3 = sum(b.volume for b in last3) / len(last3)
        qualifies = (bar.low <= ctx.ema9 and bar.close >= ctx.vwap and ctx.vol_sma is not None
                     and vol3 < ctx.vol_sma)
        cur = st.get("cur")
        if qualifies:
            if cur is None or cur["ended"]:
                st["count"] = st.get("count", 0) + 1
                self.count("pullbacks")
                cur = st["cur"] = {"n": st["count"], "low_bar": bar, "low_i": i, "ended": False, "used": False}
            elif bar.low < cur["low_bar"].low:
                cur["low_bar"], cur["low_i"] = bar, i
        elif cur is not None:
            cur["ended"] = True
        if (cur is None or cur["used"] or cur["n"] > self.p.max_pullbacks_per_day or i == cur["low_i"]
                or i - cur["low_i"] > p.pullback_max_age_bars or prev is None):
            return None
        lb = cur["low_bar"]
        if not (bar.close > lb.high and bar.volume > prev.volume):
            return None
        cur["used"] = True  # one attempt per pullback, whether or not it passes the filters below
        self.count("triggers")
        entry = round(bar.close + p.entry_offset, 4)
        stop = round(lb.low - max(p.stop_buffer_min, p.stop_buffer_atr * (ctx.atr1 or 0)), 4)
        r = entry - stop
        if r <= 0 or r / entry * 100 > p.max_stop_pct:
            self.count("rejected_stop_too_wide")
            return None
        t1 = entry + p.t1_r * r
        levels = ctx.key_levels(include_hod=True)
        blocking = {k: v for k, v in levels.items()
                    if v is not None and entry < v < t1 and (k != "hod" or p.levels_include_hod)}
        if blocking:
            self.count("rejected_key_level")
            self.count(f"rejected_key_level_{min(blocking, key=blocking.get)}")  # the level nearest to entry
            return None
        above = [v for k, v in levels.items() if k in ("hod", "prior_high") and v is not None and v > t1]
        t2 = min([entry + p.t2_r * r] + above)  # INTERPRETATION: "if nearer" = nearest of HOD / prior-day high beyond T1
        self.count("signals")
        return Signal(self.name, ctx.ticker, ctx.now, entry, stop, t1, t2,
                      {**reg, "pullback_n": cur["n"], "pullback_low": lb.low, "levels": levels},
                      time_stop_minutes=p.time_stop_minutes, time_stop_always=False,
                      flat_min=ctx.market.total_minutes - p.flat_before_close_min, elapsed=ctx.elapsed)


# --------------------------------------------------------------------------------------------------------
class Trend(Strategy):
    name, family = "B", "trend"

    def __init__(self, p: TrendParams | None = None):
        self.p = p or TrendParams()

    def _checks(self, ctx) -> list:
        if not ctx.bars5 or ctx.ema9 is None or ctx.ema20 is None or not ctx.or_ready:
            return [("warming_up", False)]
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return [("close_above_vwap", b5.close > vw), ("above_opening_range", b5.close > ctx.or_high),
                ("ema9_above_ema20", ctx.ema9 > ctx.ema20), ("vwap_rising", ctx.vwap_rising()),
                ("vwap_crosses_ok", ctx.vwap_crosses(self.p.crosses_since_min) <= self.p.max_vwap_crosses)]

    def regime(self, ctx) -> dict | None:
        if not all(ok for _, ok in self._checks(ctx)):
            return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return {"close5": b5.close, "vwap": round(vw, 4), "ema9": round(ctx.ema9, 4), "ema20": round(ctx.ema20, 4),
                "vwap_crosses": ctx.vwap_crosses(self.p.crosses_since_min), "or_high": ctx.or_high}

    def on_bar(self, ctx, new5):
        if not new5:
            return None
        p, st = self.p, self._st(ctx)
        n = len(ctx.bars5)
        b5 = ctx.bars5[-1]
        in_window = p.window_start_min <= ctx.elapsed <= p.window_end_min
        pb, st["pb"] = st.get("pb"), None
        failed = next((name for name, ok in self._checks(ctx) if not ok), None)
        reg = None if failed else self.regime(ctx)
        if in_window:
            self.count("window_bars")
            self.count(f"regime_failed_{failed}" if failed else "regime_bars")
        sig = None
        if pb and pb["i"] == n - 2 and reg and not st.get("traded") and in_window and b5.close > pb["high"]:
            self.count("triggers")
            entry = round(b5.close, 4)
            stop = round(min(pb["low"], ctx.vwap) - p.stop_buffer_atr * (ctx.atr5 or 0), 4)
            r = entry - stop
            if r > 0 and p.min_stop_pct <= r / entry * 100 <= p.max_stop_pct:
                st["traded"] = True
                self.count("signals")
                sig = Signal(self.name, ctx.ticker, ctx.now, entry, stop, entry + p.t1_r * r, None,
                             {**reg, "pullback_low": pb["low"], "pullback_high": pb["high"]},
                             flat_min=ctx.market.total_minutes - p.flat_before_close_min, elapsed=ctx.elapsed)
            else:
                self.count("rejected_stop_band")
        # is the bar that just closed a pullback bar? (touches the EMA9..EMA20 zone, holds above VWAP)
        if reg and ctx.ema9 and ctx.ema20 and b5.low <= ctx.ema9 and b5.high >= ctx.ema20 and b5.close > ctx.vwap5[-1]:
            st["pb"] = {"i": n - 1, "low": b5.low, "high": b5.high}
            if in_window:
                self.count("pullback_bars")
        return sig

    def discretionary_exit(self, pos, ctx, new5):
        if new5 and ctx.ema20 is not None and ctx.bars5[-1].close < ctx.ema20:
            return "close_below_ema20"
        return None


# --------------------------------------------------------------------------------------------------------
class Range(Strategy):
    name, family = "C", "range"

    def __init__(self, p: RangeParams | None = None):
        self.p = p or RangeParams()

    def _killed(self, ctx) -> bool:
        return bool(self._st(ctx).get("killed"))

    def on_bar(self, ctx, new5):
        p, st = self.p, self._st(ctx)
        if not new5 or not ctx.ib_ready or ctx.ib_high is None:
            return None
        w = ctx.ib_high - ctx.ib_low
        for b in new5:  # kill: any 5m close well outside the initial balance disables C for the day
            if ctx.bar5_start_min(b) >= IB_MIN and (b.close > ctx.ib_high + p.kill_band * w or b.close < ctx.ib_low - p.kill_band * w):
                st["killed"] = True
        if st.get("killed"):
            self.count_once(ctx, "days_killed")
            return None
        if not (p.window_start_min <= ctx.elapsed <= p.window_end_min):
            return None
        self.count("window_bars")
        b5 = ctx.bars5[-1]
        price = b5.close
        crosses = ctx.vwap_crosses()
        if crosses < p.min_crosses:
            self.count("regime_failed_too_few_vwap_crosses")
            return None
        if not (p.min_width_pct <= w / price * 100 <= p.max_width_pct):
            self.count("regime_failed_range_width")
            return None
        self.count("regime_bars")
        if st.get("trades", 0) >= p.max_trades_per_ticker:
            self.count("limit_trades_per_ticker")
            return None
        if not (b5.low <= ctx.ib_low + p.entry_zone * w and b5.close > b5.open and b5.close > ctx.ib_low):
            return None
        self.count("setups")
        entry = round(b5.close, 4)
        stop = round(min(b5.low, ctx.ib_low) - p.stop_buffer * w, 4)
        r = entry - stop
        t2 = round(ctx.ib_high - p.t2_inset * w, 4)
        if r <= 0 or t2 - entry < p.t2_min_r * r:
            self.count("rejected_target_too_close")
            return None
        self.count("signals")
        st["trades"] = st.get("trades", 0) + 1
        return Signal(self.name, ctx.ticker, ctx.now, entry, stop, entry + p.t1_r * r, t2,
                      {"close5": price, "vwap_crosses": crosses, "ib_high": ctx.ib_high, "ib_low": ctx.ib_low,
                       "width_pct": round(w / price * 100, 3)},
                      time_stop_minutes=p.time_stop_minutes, time_stop_always=True,
                      flat_min=ctx.market.total_minutes - p.flat_before_close_min, family="range", elapsed=ctx.elapsed)

    def discretionary_exit(self, pos, ctx, new5):
        # INTERPRETATION: the kill rule also closes any open Strategy C position at that bar's close.
        if new5:
            self.on_bar_kill_only(ctx, new5)
        return "kill_outside_ib" if self._killed(ctx) else None

    def on_bar_kill_only(self, ctx, new5):
        if ctx.ib_high is None:
            return
        w = ctx.ib_high - ctx.ib_low
        for b in new5:
            if ctx.bar5_start_min(b) >= IB_MIN and (b.close > ctx.ib_high + self.p.kill_band * w
                                                    or b.close < ctx.ib_low - self.p.kill_band * w):
                self._st(ctx)["killed"] = True


ALL = {"A": Scalp, "B": Trend, "C": Range}
