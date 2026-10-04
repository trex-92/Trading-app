"""Per-ticker, per-day market state, built incrementally one 1-minute bar at a time.

No-lookahead rule: a 5m bar exists in `bars5` only once its last 1m bar has been added, and every
indicator value is the value as of the latest CLOSED bar. `now` is the close time of the newest 1m bar.
VWAP resets each session; EMAs/ATRs/volume SMA carry across days (EMA20 on 5m needs 100 minutes to warm up).
"""
from datetime import time

from .bars import FIVE_MIN, ONE_MIN, Bar, is_premarket, minute_of_session, tod
from .indicators import Atr, Ema, RollingMean

OR_END = time(9, 45)
IB_END = time(10, 30)


class TickerState:
    """Indicator state that persists across days for one ticker."""

    def __init__(self):
        self.ema9, self.ema20 = Ema(9), Ema(20)
        self.atr5, self.atr1 = Atr(14), Atr(14)
        self.vol_sma = RollingMean(20)


class DayContext:
    def __init__(self, ticker: str, day, state: TickerState, prior: dict | None = None):
        self.ticker, self.day, self.s, self.prior = ticker, day, state, prior
        self.pm_high = self.pm_low = None
        self.bars1: list[Bar] = []
        self.bars5: list[Bar] = []
        self.vwap5: list[float] = []
        self.ema9_h: list = []
        self.ema20_h: list = []
        self.side: list[tuple] = []  # (5m bar start, +1 above / -1 below VWAP)
        self.vwap = None
        self._pv = self._vol = 0.0
        self.hod = self.lod = None
        self.or_high = self.or_low = self.ib_high = self.ib_low = None
        self._bucket: list[Bar] = []
        self._bucket_idx = None
        self.now = None
        self.scratch: dict = {}

    # ---- latest indicator values -------------------------------------------------------
    ema9 = property(lambda self: self.s.ema9.value)
    ema20 = property(lambda self: self.s.ema20.value)
    atr5 = property(lambda self: self.s.atr5.value)
    atr1 = property(lambda self: self.s.atr1.value)
    vol_sma = property(lambda self: self.s.vol_sma.value)
    or_ready = property(lambda self: self.now is not None and tod(self.now) >= OR_END)
    ib_ready = property(lambda self: self.now is not None and tod(self.now) >= IB_END)

    # ---- feeding bars ------------------------------------------------------------------
    def add_premarket(self, bar: Bar) -> None:
        if is_premarket(bar.ts):
            self.pm_high = bar.high if self.pm_high is None else max(self.pm_high, bar.high)
            self.pm_low = bar.low if self.pm_low is None else min(self.pm_low, bar.low)

    def add_1m(self, bar: Bar) -> list[Bar]:
        """Add one closed 1m bar; returns the 5m bars that closed because of it."""
        closed: list[Bar] = []
        idx = minute_of_session(bar.ts) // 5
        if self._bucket and idx != self._bucket_idx:  # gap in data: close the stale bucket first
            closed.append(self._close_bucket())
        self.bars1.append(bar)
        self.now = bar.ts + ONE_MIN
        tp = (bar.high + bar.low + bar.close) / 3
        self._pv += tp * bar.volume
        self._vol += bar.volume
        self.vwap = self._pv / self._vol if self._vol > 0 else bar.close
        self.hod = bar.high if self.hod is None else max(self.hod, bar.high)
        self.lod = bar.low if self.lod is None else min(self.lod, bar.low)
        t = tod(bar.ts)
        if t < OR_END:
            self.or_high = bar.high if self.or_high is None else max(self.or_high, bar.high)
            self.or_low = bar.low if self.or_low is None else min(self.or_low, bar.low)
        if t < IB_END:
            self.ib_high = bar.high if self.ib_high is None else max(self.ib_high, bar.high)
            self.ib_low = bar.low if self.ib_low is None else min(self.ib_low, bar.low)
        self.s.atr1.update(bar.high, bar.low, bar.close)
        self.s.vol_sma.update(bar.volume)
        self._bucket.append(bar)
        self._bucket_idx = idx
        if minute_of_session(bar.ts) + 1 >= (idx + 1) * 5:
            closed.append(self._close_bucket())
        return closed

    def _close_bucket(self) -> Bar:
        b = self._bucket
        start = b[0].ts.replace(minute=b[0].ts.minute - (minute_of_session(b[0].ts) % 5), second=0)
        bar5 = Bar(start, b[0].open, max(x.high for x in b), min(x.low for x in b), b[-1].close,
                   sum(x.volume for x in b))
        self._bucket, self._bucket_idx = [], None
        self.bars5.append(bar5)
        self.vwap5.append(self.vwap)
        self.s.ema9.update(bar5.close)
        self.s.ema20.update(bar5.close)
        self.s.atr5.update(bar5.high, bar5.low, bar5.close)
        self.ema9_h.append(self.s.ema9.value)
        self.ema20_h.append(self.s.ema20.value)
        if bar5.close != self.vwap:
            self.side.append((bar5.ts, 1 if bar5.close > self.vwap else -1))
        return bar5

    # ---- derived values used by the strategies -----------------------------------------
    def vwap_rising(self) -> bool:
        return len(self.vwap5) >= 4 and self.vwap5[-1] > self.vwap5[-4]

    def vwap_crosses(self, since: time = time(9, 30)) -> int:
        signs = [s for ts, s in self.side if tod(ts) >= since]
        return sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    def key_levels(self, include_hod: bool = False) -> dict:
        lv = {}
        if self.or_ready:
            lv.update(or_high=self.or_high, or_low=self.or_low)
        if self.ib_ready:
            lv.update(ib_high=self.ib_high, ib_low=self.ib_low)
        if self.prior:
            lv.update(prior_high=self.prior["high"], prior_low=self.prior["low"], prior_close=self.prior["close"])
        if self.pm_high is not None:
            lv.update(pm_high=self.pm_high, pm_low=self.pm_low)
        if include_hod and self.hod is not None:
            lv["hod"] = self.hod
        return lv
