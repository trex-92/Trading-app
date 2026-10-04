"""Entry-timing alpha: actual trigger outcome minus the average outcome of random entries (same symbol-day, same time of day,
same stop distance in %, same exit rules). Removes the day's drift and volatility from the comparison."""
from .scalp_lab import *

def build_daybars(data, tickers):
    out = {}
    for t in tickers:
        byday = {}
        for b in data[t]:
            if US.is_regular(b.ts): byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        for d, bs in byday.items():
            bs.sort(key=lambda b: b.ts)
            out[(t, d)] = [(US.minute_of_session(b.ts), b.open, b.high, b.low, b.close) for b in bs]
    return out

class _Sig:  # minimal stand-in for Signal
    def __init__(self, entry, stop): self.entry, self.stop = entry, stop

class _Ev:
    def __init__(self, entry, stop, fut): self.sig, self.fut, self.feats = _Sig(entry, stop), fut, {}

def null_mean(ev, bars, stop_pct, rng, n_draws=40, band=30, offset=0.02, **kw):
    mins = [b[0] for b in bars]
    k = mins.index(ev.sig.elapsed - 1) if (ev.sig.elapsed - 1) in mins else None
    if k is None: return None
    lo, hi = max(1, k - band), min(len(bars) - 2, k + band)
    cand = [j for j in range(lo, hi + 1) if j != k and bars[j][0] + 1 >= 15]
    if not cand: return None
    vals_r, vals_p = [], []
    for j in (rng.sample(cand, n_draws) if len(cand) > n_draws else cand):
        E = bars[j][4] + offset; S = E * (1 - stop_pct / 100)
        s = sim(_Ev(E, S, bars[j + 1:]), flat_min=385, hod_t2=False, **kw)
        if s: vals_r.append(s["r"]); vals_p.append(s["ret_pct"])
    return (statistics.fmean(vals_r), statistics.fmean(vals_p)) if vals_r else None

def alpha_recs(events, daybars, seed=5, **kw):
    rng = random.Random(seed); out = []
    for e in events:
        s = sim(e, flat_min=e.sig.flat_min, hod_t2=False, **kw)
        if s is None: continue
        nm = null_mean(e, daybars[(e.ticker, e.day)], e.feats["stop_pct"], rng, **kw)
        if nm is None: continue
        out.append({"day": e.day, "ticker": e.ticker, "f": e.feats, "r": s["r"], "ret_pct": s["ret_pct"],
                    "a_r": s["r"] - nm[0], "a_ret": s["ret_pct"] - nm[1], "null_r": nm[0], "null_ret": nm[1]})
    return out

def ag(recs, label):
    if not recs: return f"{label:<42} n=0"
    lo, hi = boot(recs, "a_ret"); lo2, hi2 = boot(recs, "a_r")
    return (f"{label:<42} n={len(recs):4d} actual R={statistics.fmean(r['r'] for r in recs):+.3f}  random-entry R={statistics.fmean(r['null_r'] for r in recs):+.3f}  "
            f"ALPHA R={statistics.fmean(r['a_r'] for r in recs):+.3f} [{lo2:+.2f},{hi2:+.2f}]  alpha ret={statistics.fmean(r['a_ret'] for r in recs):+.4f}% [{lo:+.3f},{hi:+.3f}]")


"""Alternative entry families, all evaluated with the same random-entry alpha test. One canonical parameterisation each (not tuned)."""

def gen_generic(entry_fn, tickers, data, spy, warmup_days=1):
    events = []
    for t in tickers:
        state, prior = TickerState(), None
        byday = {}
        for b in data[t]: byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        for di, day in enumerate(sorted(byday)):
            reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
            if not reg: continue
            ctx = DayContext(t, day, state, prior, US)
            for b in byday[day]:
                if US.is_pre(b.ts): ctx.add_premarket(b)
            mins = [US.minute_of_session(b.ts) for b in reg]; st = {}; hits = []
            for k, b in enumerate(reg):
                new5 = ctx.add_1m(b)
                if di >= warmup_days and ctx.ema20 is not None and len(ctx.bars5) >= 4:
                    r = entry_fn(ctx, new5, st, k, reg)
                    if r: hits.append((k, r))
            for k, res in hits:
                entry, stop, tag = res[:3]
                extra = res[3] if len(res) > 3 else {}          # an entry function may attach its own features
                if entry - stop <= 0: continue
                fut = [(mins[j], reg[j].open, reg[j].high, reg[j].low, reg[j].close) for j in range(k + 1, len(reg))]
                sig = Signal("X", t, ctx.now, entry, stop, 0, None, {}, flat_min=385, elapsed=mins[k] + 1)
                ev = Event(sig, t, day, fut, {"stop_pct": (entry - stop) / entry * 100, "tag": tag, "price": reg[k].close,
                                               **spy.get((day, mins[k] + 1), {}), **extra})
                events.append(ev)
            prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return events

# E-1  buy the dip: first bar of an EMA9 pullback inside the (spec) regime, no confirmation wait
def e1(regime_on):
    def fn(ctx, new5, st, k, reg):
        if not (15 <= ctx.elapsed <= 120): return None
        b = ctx.bars1[-1]
        if regime_on:
            b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
            if not (b5.close > vw and ctx.ema9 > ctx.ema20): return None
        if not (b.low <= ctx.ema9 and b.close >= ctx.vwap): return None
        if ctx.elapsed - st.get("last", -99) < 10: return None
        st["last"] = ctx.elapsed
        return (round(b.close + 0.02, 4), round(b.low - max(0.02, 0.1 * (ctx.atr1 or 0)), 4), "dip")
    return fn

# E-2  VWAP reversion: 5m close well below VWAP, then a 1m bounce bar
def e2(k_atr):
    def fn(ctx, new5, st, k, reg):
        if not (30 <= ctx.elapsed <= 330) or k < 12: return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        if not (b5.close < vw - k_atr * (ctx.atr5 or 1e9)): return None
        b, p = ctx.bars1[-1], ctx.bars1[-2]
        if not (b.close > b.open and b.close > p.high): return None
        if ctx.elapsed - st.get("last", -99) < 15: return None
        st["last"] = ctx.elapsed
        low = min(x.low for x in ctx.bars1[-10:])
        return (round(b.close + 0.02, 4), round(low - 0.1 * (ctx.atr1 or 0.02), 4), "reversion")
    return fn

# E-3  opening range breakout, long: first 5m close above the 15-minute OR high while above VWAP; stop = OR low
def e3(ctx, new5, st, k, reg):
    if st.get("done") or not new5 or not ctx.or_ready or ctx.elapsed < 20 or ctx.elapsed > 150: return None
    b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
    if b5.close > ctx.or_high and b5.close > vw:
        st["done"] = True
        return (round(b5.close + 0.02, 4), round(ctx.or_low - 0.02, 4), "orb")
    return None

# E-4  trend-day momentum: new high of day on a 5m close, above VWAP, EMA9>EMA20, after 10:15
def e4(ctx, new5, st, k, reg):
    if not new5 or not (45 <= ctx.elapsed <= 240): return None
    b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
    prev_hi = max(b.high for b in ctx.bars5[:-1]) if len(ctx.bars5) > 1 else None
    if prev_hi and b5.close > prev_hi and b5.close > vw and ctx.ema9 > ctx.ema20 and ctx.elapsed - st.get("last", -99) >= 30:
        st["last"] = ctx.elapsed
        return (round(b5.close + 0.02, 4), round(min(b5.low, ctx.vwap) - 0.1 * (ctx.atr5 or 0), 4), "hod_break")
    return None

@dataclass(frozen=True)
class XD:
    window_start_min: int = 30
    window_end_min: int = 330
    k_atr: float = 1.5            # how far below VWAP (in 5-minute ATRs) counts as oversold
    need_bounce: bool = True      # 1m close above the previous high and above its open
    cooldown_min: int = 15
    stop_mode: str = "swing"      # swing: lowest low of last 10 minutes; atr: atr_stop x ATR5 below entry
    atr_stop: float = 1.0
    stop_buffer_atr: float = 0.1
    t1_r: float = 1.5
    t2_r: float = 2.5
    target_vwap: bool = False     # T2 = VWAP, T1 = halfway to VWAP (when VWAP is above entry + 1R)
    t1_frac: float = 0.5
    time_stop_minutes: int = 25
    flat_before_close_min: int = 5
    max_stop_pct: float = 1.5

class ReversionX(Strategy):
    name, family = "D", "range"
    def __init__(self, p=None): self.p = p or XD()
    def on_bar(self, ctx, new5):
        p, st = self.p, self._st(ctx)
        if not (p.window_start_min <= ctx.elapsed <= p.window_end_min) or len(ctx.bars5) < 4 or ctx.ema20 is None or len(ctx.bars1) < 13:
            return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        if not (b5.close < vw - p.k_atr * (ctx.atr5 or 1e9)): return None
        b, pv = ctx.bars1[-1], ctx.bars1[-2]
        if p.need_bounce and not (b.close > b.open and b.close > pv.high): return None
        if ctx.elapsed - st.get("last", -99) < p.cooldown_min: return None
        entry = round(b.close + 0.02, 4)
        if p.stop_mode == "swing":
            stop = round(min(x.low for x in ctx.bars1[-10:]) - p.stop_buffer_atr * (ctx.atr1 or 0.02), 4)
        else:
            stop = round(entry - p.atr_stop * (ctx.atr5 or 0), 4)
        r = entry - stop
        if r <= 0 or r / entry * 100 > p.max_stop_pct: return None
        st["last"] = ctx.elapsed
        t1, t2 = entry + p.t1_r * r, entry + p.t2_r * r
        if p.target_vwap and ctx.vwap > entry + r:
            t2 = ctx.vwap; t1 = entry + (ctx.vwap - entry) / 2
        return Signal(self.name, ctx.ticker, ctx.now, entry, stop, t1, t2,
                      {"dist_atr": round((vw - b5.close) / (ctx.atr5 or 1), 2), "vwap": round(ctx.vwap, 4)}, t1_frac=p.t1_frac,
                      time_stop_minutes=p.time_stop_minutes, time_stop_always=False,
                      flat_min=ctx.market.total_minutes - p.flat_before_close_min, family="range", elapsed=ctx.elapsed)


# E2x  VWAP reversion that also records the depth below VWAP (in ATR5 units) and other context for dose-response checks
def e2x(k_min=1.0, cooldown=15, need_bounce=True, start=30, end=330):
    def fn(ctx, new5, st, k, reg):
        if not (start <= ctx.elapsed <= end) or k < 12: return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        depth = (vw - b5.close) / (ctx.atr5 or 1e9)
        if depth < k_min: return None
        b, p = ctx.bars1[-1], ctx.bars1[-2]
        if need_bounce and not (b.close > b.open and b.close > p.high): return None
        if ctx.elapsed - st.get("last", -99) < cooldown: return None
        st["last"] = ctx.elapsed
        low = min(x.low for x in ctx.bars1[-10:])
        extra = {"depth": depth, "elapsed": ctx.elapsed, "atr5_pct": (ctx.atr5 or 0) / b.close * 100,
                 "ret_open": (b.close / ctx.bars1[0].open - 1) * 100}
        return (round(b.close + 0.02, 4), round(low - 0.1 * (ctx.atr1 or 0.02), 4), "reversion", extra)
    return fn


# E5  other "oversold" definitions with the same bounce bar, stop and exits as e2/e2x
def _rsi(vals, n=14):
    if len(vals) < n + 1: return None
    gains = [max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals))]
    losses = [max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals))]
    ag_, al_ = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag_ = (ag_ * (n - 1) + g) / n
        al_ = (al_ * (n - 1) + l) / n
    return 100.0 if al_ == 0 else 100 - 100 / (1 + ag_ / al_)


def e5(kind, thresh, cooldown=15):
    """kind 'rsi': 5-minute RSI(14) of today's closes below thresh; 'bb': 5-minute close below the 20-bar mean minus thresh sigmas."""
    def fn(ctx, new5, st, k, reg):
        if not (30 <= ctx.elapsed <= 330) or k < 12: return None
        closes = [b.close for b in ctx.bars5]
        if kind == "rsi":
            v = _rsi(closes, 14)
            if v is None or v > thresh: return None
        else:
            if len(closes) < 20: return None
            w = closes[-20:]
            m, sd = statistics.fmean(w), statistics.pstdev(w)
            if sd == 0 or closes[-1] > m - thresh * sd: return None
        b, p = ctx.bars1[-1], ctx.bars1[-2]
        if not (b.close > b.open and b.close > p.high): return None
        if ctx.elapsed - st.get("last", -99) < cooldown: return None
        st["last"] = ctx.elapsed
        low = min(x.low for x in ctx.bars1[-10:])
        return (round(b.close + 0.02, 4), round(low - 0.1 * (ctx.atr1 or 0.02), 4), kind)
    return fn
