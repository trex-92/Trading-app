"""Per-ticker, per-day market state, built incrementally one 1-minute bar at a time.

No-lookahead rule: a 5m bar exists in `bars5` only once its last 1m bar has been added, and every indicator value is the
value as of the latest CLOSED bar. `elapsed` is the number of TRADING minutes completed (a lunch break does not count),
so the opening range is the first 15 and the initial balance the first 60 trading minutes in every market.
VWAP resets each session; EMAs/ATRs/volume SMA carry across days (EMA20 on 5m needs 100 trading minutes to warm up).
"""
from .bars import ONE_MIN, Bar
from .indicators import Atr, Ema, RollingMean
from .market import US, Market

OR_MIN = 15   # opening range = first 15 trading minutes
IB_MIN = 60   # initial balance = first 60 trading minutes


class TickerState:
    """Indicator state that persists across days for one ticker."""

    def __init__(self):
        self.ema9, self.ema20 = Ema(9), Ema(20)
        self.atr5, self.atr1 = Atr(14), Atr(14)
        self.vol_sma = RollingMean(20)


class DayContext:
    def __init__(self, ticker: str, day, state: TickerState, prior: dict | None = None, market: Market = US):
        self.ticker, self.day, self.s, self.prior, self.market = ticker, day, state, prior, market
        self.pm_high = self.pm_low = None
        self.bars1: list[Bar] = []
        self.bars5: list[Bar] = []
        self.vwap5: list[float] = []
        self.ema9_h: list = []
        self.ema20_h: list = []
        self.side: list[tuple] = []  # (trading minute the 5m bar started, +1 above / -1 below VWAP)
        self.vwap = None
        self._pv = self._vol = 0.0
        self.hod = self.lod = None
        self.or_high = self.or_low = self.ib_high = self.ib_low = None
        self._bucket: list[Bar] = []
        self._bucket_idx = None
        self.now = None
        self.elapsed = 0
        self.scratch: dict = {}

    # ---- latest indicator values -------------------------------------------------------
    ema9 = property(lambda self: self.s.ema9.value)
    ema20 = property(lambda self: self.s.ema20.value)
    atr5 = property(lambda self: self.s.atr5.value)
    atr1 = property(lambda self: self.s.atr1.value)
    vol_sma = property(lambda self: self.s.vol_sma.value)
    or_ready = property(lambda self: self.elapsed >= OR_MIN)
    ib_ready = property(lambda self: self.elapsed >= IB_MIN)

    # ---- feeding bars ------------------------------------------------------------------
    def add_premarket(self, bar: Bar) -> None:
        if self.market.is_pre(bar.ts):
            self.pm_high = bar.high if self.pm_high is None else max(self.pm_high, bar.high)
            self.pm_low = bar.low if self.pm_low is None else min(self.pm_low, bar.low)

    def add_1m(self, bar: Bar) -> list[Bar]:
        """Add one closed 1m bar; returns the 5m bars that closed because of it."""
        closed: list[Bar] = []
        m = self.market.minute_of_session(bar.ts)
        if m is None:
            raise ValueError(f"bar {bar.ts} is outside {self.market.code} trading hours")
        idx = m // 5
        if self._bucket and idx != self._bucket_idx:  # gap in data: close the stale bucket first
            closed.append(self._close_bucket())
        self.bars1.append(bar)
        self.now = bar.ts + ONE_MIN
        self.elapsed = m + 1
        tp = (bar.high + bar.low + bar.close) / 3
        self._pv += tp * bar.volume
        self._vol += bar.volume
        self.vwap = self._pv / self._vol if self._vol > 0 else bar.close
        self.hod = bar.high if self.hod is None else max(self.hod, bar.high)
        self.lod = bar.low if self.lod is None else min(self.lod, bar.low)
        if m < OR_MIN:
            self.or_high = bar.high if self.or_high is None else max(self.or_high, bar.high)
            self.or_low = bar.low if self.or_low is None else min(self.or_low, bar.low)
        if m < IB_MIN:
            self.ib_high = bar.high if self.ib_high is None else max(self.ib_high, bar.high)
            self.ib_low = bar.low if self.ib_low is None else min(self.ib_low, bar.low)
        self.s.atr1.update(bar.high, bar.low, bar.close)
        self.s.vol_sma.update(bar.volume)
        self._bucket.append(bar)
        self._bucket_idx = idx
        if m + 1 >= (idx + 1) * 5:
            closed.append(self._close_bucket())
        return closed

    def _close_bucket(self) -> Bar:
        b = self._bucket
        m0 = self.market.minute_of_session(b[0].ts)
        start_min = m0 - m0 % 5
        start = b[0].ts - (m0 - start_min) * ONE_MIN  # aligned start (segments are whole numbers of 5 minutes)
        bar5 = Bar(start, b[0].open, max(x.high for x in b), min(x.low for x in b), b[-1].close, sum(x.volume for x in b))
        self._bucket, self._bucket_idx = [], None
        self.bars5.append(bar5)
        self.vwap5.append(self.vwap)
        self.s.ema9.update(bar5.close)
        self.s.ema20.update(bar5.close)
        self.s.atr5.update(bar5.high, bar5.low, bar5.close)
        self.ema9_h.append(self.s.ema9.value)
        self.ema20_h.append(self.s.ema20.value)
        if bar5.close != self.vwap:
            self.side.append((start_min, 1 if bar5.close > self.vwap else -1))
        return bar5

    # ---- derived values used by the strategies -----------------------------------------
    def bar5_start_min(self, bar5: Bar) -> int:
        return self.market.minute_of_session(bar5.ts)

    def vwap_rising(self) -> bool:
        return len(self.vwap5) >= 4 and self.vwap5[-1] > self.vwap5[-4]

    def vwap_crosses(self, since_min: int = 0) -> int:
        signs = [s for m, s in self.side if m >= since_min and s != 0]
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
