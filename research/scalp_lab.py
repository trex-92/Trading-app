"""Event-level lab for Strategy A (VWAP pullback scalp): ScalpX (same logic as the repo's Scalp, with switches and feature
capture), a per-trade simulator that replicates the backtester's fills and exits, and the statistics helpers."""
from dataclasses import dataclass, field, replace
from .common import *
from bot.intraday.bars import ONE_MIN
from bot.intraday.context import DayContext, TickerState
from bot.intraday.strategies import Strategy, Signal

@dataclass(frozen=True)
class XP:
    window_start_min: int = 15
    window_end_min: int = 120
    reg_close_above_vwap: bool = True
    reg_vwap_rising: bool = True
    reg_ema_order: bool = True
    reg_emas_rising: bool = True
    reg_crosses: bool = True
    max_vwap_crosses: int = 2
    reg_or_break: bool = True
    max_pullbacks_per_day: int = 2
    pullback_max_age_bars: int = 10
    pb_vol_dip: bool = True
    trig_vol_up: bool = True
    entry_offset: float = 0.02
    stop_buffer_min: float = 0.02
    stop_buffer_atr: float = 0.1
    max_stop_pct: float = 0.25        # 0 = no limit
    block_levels: bool = True
    levels_include_hod: bool = False
    t1_r: float = 1.5
    t2_r: float = 2.5
    t1_frac: float = 0.5
    time_stop_minutes: int = 25
    flat_before_close_min: int = 5
    capture: bool = False

class ScalpX(Strategy):
    name, family = "A", "trend"
    def __init__(self, p=None, spy=None):
        self.p = p or XP(); self.spy = spy or {}

    def _flags(self, ctx):
        if (len(ctx.bars5) < 2 or ctx.ema9 is None or ctx.ema20 is None or not ctx.or_ready
                or ctx.ema9_h[-2] is None or ctx.ema20_h[-2] is None):
            return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return {"close_above_vwap": b5.close > vw, "vwap_rising": ctx.vwap_rising(), "ema_order": ctx.ema9 > ctx.ema20,
                "emas_rising": ctx.ema9 > ctx.ema9_h[-2] and ctx.ema20 > ctx.ema20_h[-2], "crosses": ctx.vwap_crosses(),
                "or_break": any(b.close > ctx.or_high for b in ctx.bars5)}

    def on_bar(self, ctx, new5):
        p = self.p; st = self._st(ctx)
        if not (p.window_start_min <= ctx.elapsed <= p.window_end_min):
            return None
        fl = self._flags(ctx)
        if fl is None:
            st["cur"] = None; return None
        if ((p.reg_close_above_vwap and not fl["close_above_vwap"]) or (p.reg_vwap_rising and not fl["vwap_rising"])
                or (p.reg_ema_order and not fl["ema_order"]) or (p.reg_emas_rising and not fl["emas_rising"])
                or (p.reg_crosses and fl["crosses"] > p.max_vwap_crosses) or (p.reg_or_break and not fl["or_break"])):
            st["cur"] = None; return None
        bar, i = ctx.bars1[-1], len(ctx.bars1) - 1
        prev = ctx.bars1[-2] if i >= 1 else None
        last3 = ctx.bars1[-3:]
        vol3 = sum(b.volume for b in last3) / len(last3)
        qualifies = bar.low <= ctx.ema9 and bar.close >= ctx.vwap and (not p.pb_vol_dip or (ctx.vol_sma is not None and vol3 < ctx.vol_sma))
        cur = st.get("cur")
        if qualifies:
            if cur is None or cur["ended"]:
                st["count"] = st.get("count", 0) + 1
                cur = st["cur"] = {"n": st["count"], "low_bar": bar, "low_i": i, "ended": False, "used": False}
            elif bar.low < cur["low_bar"].low:
                cur["low_bar"], cur["low_i"] = bar, i
        elif cur is not None:
            cur["ended"] = True
        if (cur is None or cur["used"] or cur["n"] > p.max_pullbacks_per_day or i == cur["low_i"]
                or i - cur["low_i"] > p.pullback_max_age_bars or prev is None):
            return None
        lb = cur["low_bar"]
        if not (bar.close > lb.high and (not p.trig_vol_up or bar.volume > prev.volume)):
            return None
        cur["used"] = True
        entry = round(bar.close + p.entry_offset, 4)
        stop = round(lb.low - max(p.stop_buffer_min, p.stop_buffer_atr * (ctx.atr1 or 0)), 4)
        r = entry - stop
        if r <= 0:
            return None
        if p.max_stop_pct and r / entry * 100 > p.max_stop_pct:
            return None
        t1 = entry + p.t1_r * r
        levels = ctx.key_levels(include_hod=True)
        blocking = {k: v for k, v in levels.items() if v is not None and entry < v < t1 and (k != "hod" or p.levels_include_hod)}
        if p.block_levels and blocking:
            return None
        above = [v for k, v in levels.items() if k in ("hod", "prior_high") and v is not None and v > t1]
        t2 = min([entry + p.t2_r * r] + above)
        reg = {}
        if p.capture:
            reg = self._features(ctx, bar, i, entry, stop, r, levels, cur, fl, lb)
        return Signal(self.name, ctx.ticker, ctx.now, entry, stop, t1, t2, reg, t1_frac=p.t1_frac,
                      time_stop_minutes=p.time_stop_minutes, time_stop_always=False,
                      flat_min=ctx.market.total_minutes - p.flat_before_close_min, elapsed=ctx.elapsed)

    def _features(self, ctx, bar, i, entry, stop, r, levels, cur, fl, lb):
        price = bar.close; day_open = ctx.bars1[0].open
        t1s = entry + 1.5 * r
        blk = {k: v for k, v in levels.items() if v is not None and entry < v < t1s and k != "hod"}
        up = {k: v for k, v in levels.items() if v is not None and v > entry and k != "hod"}
        nk = min(up, key=up.get) if up else None
        pr = ctx.prior
        f = {"elapsed": ctx.elapsed, "price": price, "stop_pct": r / entry * 100,
             "r_over_atr1": r / ctx.atr1 if ctx.atr1 else None, "atr1_pct": (ctx.atr1 or 0) / price * 100,
             "atr5_pct": (ctx.atr5 or 0) / price * 100, "ema_gap_pct": (ctx.ema9 - ctx.ema20) / price * 100,
             "vwap_slope_pct": (ctx.vwap5[-1] / ctx.vwap5[-4] - 1) * 100 if len(ctx.vwap5) >= 4 else None,
             "dist_vwap_pct": (price - ctx.vwap) / price * 100, "dist_vwap_R": (price - ctx.vwap) / r,
             "trig_vol_ratio": bar.volume / ctx.vol_sma if ctx.vol_sma else None,
             "pb_n": cur["n"], "pb_age": i - cur["low_i"], "n_block": len(blk), "near_level": nk,
             "near_level_R": (levels[nk] - entry) / r if nk else None, "hod_R": (ctx.hod - entry) / r,
             "gap_pct": (day_open / pr["close"] - 1) * 100 if pr else None, "ret_open_pct": (price / day_open - 1) * 100,
             "or_range_pct": (ctx.or_high - ctx.or_low) / price * 100, "above_or_high": price > ctx.or_high,
             "hod": ctx.hod, "prior_high": pr["high"] if pr else None, "pm_high": ctx.pm_high,
             **{"fl_" + k: v for k, v in fl.items()}}
        f.update(self.spy.get((ctx.day, ctx.elapsed), {}))
        return f

def spy_table(data, ticker="SPY"):
    """Market context per (day, elapsed minutes), built with the same no-lookahead DayContext."""
    out, state, prior = {}, TickerState(), None
    byday = {}
    for b in data[ticker]:
        byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
    for di, day in enumerate(sorted(byday)):
        ctx = DayContext(ticker, day, state, prior, US)
        reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
        for b in byday[day]:
            if US.is_pre(b.ts): ctx.add_premarket(b)
        if not reg: continue
        for b in reg:
            ctx.add_1m(b)
            if ctx.vwap5 and len(ctx.vwap5) >= 4:
                out[(day, ctx.elapsed)] = {"spy_ret_open_pct": (b.close / ctx.bars1[0].open - 1) * 100,
                    "spy_above_vwap": ctx.bars5[-1].close > ctx.vwap5[-1], "spy_vwap_rising": ctx.vwap_rising(),
                    "spy_ema_order": (ctx.ema9 or 0) > (ctx.ema20 or 1e18)}
        prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return out

@dataclass
class Event:
    sig: Signal
    ticker: str
    day: date
    fut: list           # [(minute_index, o, h, l, c)] bars AFTER the trigger bar, same session
    feats: dict

def gen_events(p, tickers, data, spy=None, warmup_days=1):
    events = []
    for t in tickers:
        strat = ScalpX(p, spy)
        state, prior = TickerState(), None
        byday = {}
        for b in data[t]:
            byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        for di, day in enumerate(sorted(byday)):
            reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
            if not reg: continue
            ctx = DayContext(t, day, state, prior, US)
            for b in byday[day]:
                if US.is_pre(b.ts): ctx.add_premarket(b)
            mins = [US.minute_of_session(b.ts) for b in reg]
            for k, b in enumerate(reg):
                new5 = ctx.add_1m(b)
                if di >= warmup_days:
                    sig = strat.on_bar(ctx, new5)
                    if sig:
                        fut = [(mins[j], reg[j].open, reg[j].high, reg[j].low, reg[j].close) for j in range(k + 1, len(reg))]
                        events.append(Event(sig, t, day, fut, sig.regime))
            prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return events

def sim(ev, t1_r=1.5, t2_r=2.5, t1_frac=0.5, ts_min=25, ts_always=False, flat_min=385, cost_ps=0.02, hod_t2=True,
        stop_mult=1.0, be_after_t1=True, trail_r=None, fill_mode="limit", slip=0.01):
    """Replicates Backtester._try_fill/_manage for ONE trade per share (no position limits). Returns dict or None (no fill)."""
    E, S0 = ev.sig.entry, ev.sig.stop
    R0 = E - S0
    S = E - stop_mult * R0              # optionally scale the stop distance (targets stay in units of the ORIGINAL R)
    R = E - S
    T1 = E + t1_r * R
    f = ev.feats
    above = [v for v in ((f.get("hod"), f.get("prior_high")) if hod_t2 else ()) if v is not None and v > T1]
    T2 = min([E + t2_r * R] + above) if t2_r else None
    fut = ev.fut
    if not fut: return None
    m0, o, h, l, c = fut[0]
    if fill_mode == "market": fill = o + slip
    elif o <= E: fill = o
    elif l <= E: fill = E
    else: return None
    stop, rem, t1_done, pnl = S, 1.0, False, 0.0
    mfe = mae = 0.0; exit_reason = None; held = 0; exit_px = None; peak = fill
    for (m, o, h, l, c) in fut:
        elapsed = m + 1; held = elapsed - m0
        mfe = max(mfe, (h - fill) / R); mae = min(mae, (l - fill) / R)
        if o <= stop:
            pnl += rem * (o - fill); rem = 0; exit_reason = "stop_gap"; break
        if l <= stop:
            pnl += rem * (stop - fill); rem = 0; exit_reason = "breakeven_stop" if t1_done else "stop"; break
        if not t1_done and h >= T1:
            q = t1_frac
            pnl += q * (max(T1, o) - fill); rem -= q; t1_done = True
            if be_after_t1: stop = fill
        if t1_done and T2 and h >= T2:
            pnl += rem * (max(T2, o) - fill); rem = 0; exit_reason = "target2"; break
        if trail_r and t1_done:
            peak = max(peak, h); stop = max(stop, peak - trail_r * R)
        reason = None
        if ts_min and held >= ts_min and (ts_always or not t1_done): reason = "time_stop"
        if not reason and elapsed >= flat_min: reason = "flat_eod"
        if reason:
            pnl += rem * (c - fill); rem = 0; exit_reason = reason; break
    if rem > 0:
        pnl += rem * (fut[-1][4] - fill); exit_reason = "end_of_data"
    net = pnl - cost_ps
    return {"r": net / R0, "ret_pct": net / fill * 100, "reason": exit_reason, "held": held, "mfe": mfe, "mae": mae,
            "t1": t1_done, "fill": fill}

def path_stats(ev, horizons=(15, 30, 60)):
    """Max favourable / adverse excursion in units of the planned R over the next N minutes (ignores stops/exits)."""
    E, R = ev.sig.entry, ev.sig.entry - ev.sig.stop
    out = {}
    for hz in horizons:
        seg = ev.fut[:hz]
        if not seg: out[hz] = (None, None); continue
        out[hz] = ((max(x[2] for x in seg) - E) / R, (min(x[3] for x in seg) - E) / R)
    return out

def recs_of(events, **kw):
    out = []
    for e in events:
        s = sim(e, flat_min=e.sig.flat_min, **kw)
        if s is None: continue
        out.append({**s, "day": e.day, "ticker": e.ticker, "f": e.feats, "ev": e})
    return out

def boot(recs, key="r", reps=2000, seed=1):
    """Day-clustered bootstrap CI for the mean of `key` (events on the same day are correlated, so resample DAYS)."""
    by = defaultdict(lambda: [0.0, 0])
    for r in recs:
        by[r["day"]][0] += r[key]; by[r["day"]][1] += 1
    days = list(by.values())
    if len(days) < 3: return (float("nan"), float("nan"))
    rng = random.Random(seed); ms = []
    for _ in range(reps):
        s = c = 0
        for _ in days:
            d = days[rng.randrange(len(days))]; s += d[0]; c += d[1]
        if c: ms.append(s / c)
    ms.sort()
    return ms[int(0.025 * len(ms))], ms[int(0.975 * len(ms))]

def agg(recs, label="", ci=True):
    n = len(recs)
    if n == 0: return f"{label:<40} n=0"
    rs = [r["r"] for r in recs]
    w = [x for x in rs if x > 0]; l = [x for x in rs if x < 0]
    pf = sum(w) / -sum(l) if l else float("inf")
    lo, hi = boot(recs) if ci and n >= 8 else (float("nan"), float("nan"))
    ret = statistics.fmean(r["ret_pct"] for r in recs)
    return (f"{label:<40} n={n:4d} win={len(w)/n*100:5.1f}% avgR={statistics.fmean(rs):+.3f} [{lo:+.2f},{hi:+.2f}] "
            f"PF={pf:4.2f} ret/trade={ret:+.3f}%")

