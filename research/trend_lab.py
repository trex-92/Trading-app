"""Event studies for Strategy B (trend) and C (range) via the REAL engine, one ticker at a time, risk limits relaxed."""
from .scalp_lab import *
from bot.intraday.params import apply_overrides, TrendParams, RangeParams
from bot.intraday.strategies import Trend, Range

RELAX = {"max_trades_per_day": 99, "stop_after_consecutive_losses": 99, "daily_max_loss_pct": 100.0, "max_cost_pct_of_1R": 1e9}

@dataclass(frozen=True)
class XB:
    window_start_min: int = 30
    window_end_min: int = 150
    reg_close_above_vwap: bool = True
    reg_above_or: bool = True
    reg_ema_order: bool = True
    reg_vwap_rising: bool = True
    reg_crosses: bool = True
    max_vwap_crosses: int = 1
    crosses_since_min: int = 15
    stop_buffer_atr: float = 0.1
    min_stop_pct: float = 0.3
    max_stop_pct: float = 1.0          # 0 = off
    t1_r: float = 2.0
    exit_ema20: bool = True
    flat_before_close_min: int = 5
    one_per_day: bool = True
    capture: bool = False

class TrendX(Strategy):
    name, family = "B", "trend"
    def __init__(self, p=None, spy=None):
        self.p = p or XB(); self.spy = spy or {}
    def _flags(self, ctx):
        if not ctx.bars5 or ctx.ema9 is None or ctx.ema20 is None or not ctx.or_ready:
            return None
        b5, vw = ctx.bars5[-1], ctx.vwap5[-1]
        return {"close_above_vwap": b5.close > vw, "above_or": b5.close > ctx.or_high, "ema_order": ctx.ema9 > ctx.ema20,
                "vwap_rising": ctx.vwap_rising(), "crosses": ctx.vwap_crosses(self.p.crosses_since_min)}
    def _reg(self, fl):
        p = self.p
        return fl is not None and not ((p.reg_close_above_vwap and not fl["close_above_vwap"]) or (p.reg_above_or and not fl["above_or"])
            or (p.reg_ema_order and not fl["ema_order"]) or (p.reg_vwap_rising and not fl["vwap_rising"])
            or (p.reg_crosses and fl["crosses"] > p.max_vwap_crosses))
    def on_bar(self, ctx, new5):
        if not new5: return None
        p, st = self.p, self._st(ctx)
        n = len(ctx.bars5); b5 = ctx.bars5[-1]
        in_window = p.window_start_min <= ctx.elapsed <= p.window_end_min
        pb, st["pb"] = st.get("pb"), None
        fl = self._flags(ctx); reg = self._reg(fl)
        sig = None
        if pb and pb["i"] == n - 2 and reg and not (p.one_per_day and st.get("traded")) and in_window and b5.close > pb["high"]:
            entry = round(b5.close, 4)
            stop = round(min(pb["low"], ctx.vwap) - p.stop_buffer_atr * (ctx.atr5 or 0), 4)
            r = entry - stop
            ok = r > 0 and (not p.min_stop_pct or r / entry * 100 >= p.min_stop_pct) and (not p.max_stop_pct or r / entry * 100 <= p.max_stop_pct)
            if ok:
                st["traded"] = True
                feats = self._features(ctx, b5, entry, stop, r, pb, fl) if p.capture else {}
                sig = Signal(self.name, ctx.ticker, ctx.now, entry, stop, entry + p.t1_r * r, None, feats,
                             flat_min=ctx.market.total_minutes - p.flat_before_close_min, elapsed=ctx.elapsed)
        if reg and ctx.ema9 and ctx.ema20 and b5.low <= ctx.ema9 and b5.high >= ctx.ema20 and b5.close > ctx.vwap5[-1]:
            st["pb"] = {"i": n - 1, "low": b5.low, "high": b5.high, "pbbar": b5}
        return sig
    def _features(self, ctx, b5, entry, stop, r, pb, fl):
        price = b5.close; day_open = ctx.bars1[0].open; pr = ctx.prior
        vols = [b.volume for b in ctx.bars5[-8:-1]]
        f = {"elapsed": ctx.elapsed, "price": price, "stop_pct": r / entry * 100, "atr5_pct": (ctx.atr5 or 0) / price * 100,
             "r_over_atr5": r / ctx.atr5 if ctx.atr5 else None, "ema_gap_pct": (ctx.ema9 - ctx.ema20) / price * 100,
             "vwap_slope_pct": (ctx.vwap5[-1] / ctx.vwap5[-4] - 1) * 100 if len(ctx.vwap5) >= 4 else None,
             "dist_vwap_pct": (price - ctx.vwap) / price * 100, "dist_or_high_pct": (price - ctx.or_high) / price * 100,
             "trig_vol_ratio": b5.volume / (sum(vols) / len(vols)) if vols else None,
             "pb_range_atr": (pb["high"] - pb["low"]) / ctx.atr5 if ctx.atr5 else None,
             "trig_range_atr": (b5.high - b5.low) / ctx.atr5 if ctx.atr5 else None,
             "hod_R": (ctx.hod - entry) / r, "gap_pct": (day_open / pr["close"] - 1) * 100 if pr else None,
             "ret_open_pct": (price / day_open - 1) * 100, "or_range_pct": (ctx.or_high - ctx.or_low) / price * 100,
             **{"fl_" + k: v for k, v in fl.items()}}
        f.update(self.spy.get((ctx.day, ctx.elapsed), {}))
        return f
    def discretionary_exit(self, pos, ctx, new5):
        if self.p.exit_ema20 and new5 and ctx.ema20 is not None and ctx.bars5[-1].close < ctx.ema20:
            return "close_below_ema20"
        return None

def engine_events(factory, tickers, data, budget=1_000_000.0, shared_over=None):
    """Run the real Backtester one ticker at a time (positions in different tickers do not block each other)."""
    out = []
    sh = apply_overrides(base_shared(US), {**RELAX, **(shared_over or {})})
    for t in tickers:
        bt = Backtester([factory()], sh, budget, Calendar(), market=US)
        res = bt.run({t: data[t]})
        for tr in res.trades:
            tr["ret_pct"] = tr["pnl"] / (tr["shares"] * tr["entry"]) * 100
            tr["day"] = date.fromisoformat(tr["date"]); tr["f"] = tr["regime"]
            out.append(tr)
    return out

def summ_t(trs, label, ci=True):
    return agg(trs, label, ci)


"""Per-trade simulator for Strategy B events (needs per-minute EMA20/EMA9/VWAP after the trigger)."""

def gen_events_b(p, tickers, data, spy=None, warmup_days=1):
    events = []
    for t in tickers:
        strat = TrendX(p, spy); state, prior = TickerState(), None
        byday = {}
        for b in data[t]: byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        for di, day in enumerate(sorted(byday)):
            reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
            if not reg: continue
            ctx = DayContext(t, day, state, prior, US)
            for b in byday[day]:
                if US.is_pre(b.ts): ctx.add_premarket(b)
            mins = [US.minute_of_session(b.ts) for b in reg]; ex = []; hits = []
            for k, b in enumerate(reg):
                new5 = ctx.add_1m(b)
                ex.append((bool(new5), ctx.bars5[-1].close if ctx.bars5 else None, ctx.ema20, ctx.ema9, ctx.vwap))
                if di >= warmup_days:
                    sig = strat.on_bar(ctx, new5)
                    if sig: hits.append((k, sig))
            for k, sig in hits:
                fut = [(mins[j], reg[j].open, reg[j].high, reg[j].low, reg[j].close) + ex[j] for j in range(k + 1, len(reg))]
                events.append(Event(sig, t, day, fut, sig.regime))
            prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return events

def sim_b(ev, t1_r=2.0, t1_frac=0.5, exit_mode="ema20", ts_min=None, ts_always=False, be=True, cost_ps=0.02, flat_min=385):
    E, S = ev.sig.entry, ev.sig.stop; R = E - S
    T1 = E + t1_r * R
    fut = ev.fut
    if not fut: return None
    m0, o, h, l, c = fut[0][:5]
    if o <= E: fill = o
    elif l <= E: fill = E
    else: return None
    stop, rem, t1_done, pnl, reason, held = S, 1.0, False, 0.0, None, 0
    for x in fut:
        m, o, h, l, c, new5, c5, e20, e9, vw = x
        elapsed = m + 1; held = elapsed - m0
        if o <= stop: pnl += rem * (o - fill); rem = 0; reason = "stop_gap"; break
        if l <= stop: pnl += rem * (stop - fill); rem = 0; reason = "breakeven_stop" if t1_done else "stop"; break
        if not t1_done and h >= T1:
            pnl += t1_frac * (max(T1, o) - fill); rem -= t1_frac; t1_done = True
            if rem <= 1e-9: rem = 0; reason = "target1"; break
            if be: stop = fill
        r_ = None
        if new5 and exit_mode != "none":
            if exit_mode == "ema20" and e20 is not None and c5 < e20: r_ = "ema20"
            elif exit_mode == "ema9" and e9 is not None and c5 < e9: r_ = "ema9"
            elif exit_mode == "vwap" and c5 < vw: r_ = "vwap"
        if not r_ and ts_min and held >= ts_min and (ts_always or not t1_done): r_ = "time_stop"
        if not r_ and elapsed >= flat_min: r_ = "flat_eod"
        if r_: pnl += rem * (c - fill); rem = 0; reason = r_; break
    if rem > 0: pnl += rem * (fut[-1][4] - fill); reason = "end_of_data"
    net = pnl - cost_ps
    return {"r": net / R, "ret_pct": net / fill * 100, "reason": reason, "held": held, "fill": fill}


# ---- random-entry alpha for Trend (B): same idea as research.alpha, but entries are 5-minute bar closes and exits are B's ----
def build_dayx(data, tickers):
    """{(ticker, day): (minutes, bars, extras)} with the per-minute extras sim_b needs (new 5m bar?, 5m close, EMA20, EMA9, VWAP)."""
    out = {}
    for t in tickers:
        state, prior = TickerState(), None
        byday = {}
        for b in data[t]:
            byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        for day in sorted(byday):
            reg = sorted([b for b in byday[day] if US.is_regular(b.ts)], key=lambda b: b.ts)
            if not reg:
                continue
            ctx = DayContext(t, day, state, prior, US)
            for b in byday[day]:
                if US.is_pre(b.ts):
                    ctx.add_premarket(b)
            mins, bars, ex = [], [], []
            for b in reg:
                new5 = ctx.add_1m(b)
                mins.append(US.minute_of_session(b.ts))
                bars.append((mins[-1], b.open, b.high, b.low, b.close))
                ex.append((bool(new5), ctx.bars5[-1].close if ctx.bars5 else None, ctx.ema20, ctx.ema9, ctx.vwap))
            out[(t, day)] = (mins, bars, ex)
            prior = {"high": ctx.hod, "low": ctx.lod, "close": ctx.bars1[-1].close}
    return out


class _BSig:
    def __init__(self, entry, stop): self.entry, self.stop = entry, stop


class _BEv:
    def __init__(self, entry, stop, fut): self.sig, self.fut = _BSig(entry, stop), fut


def b_alpha_recs(events, dayx, n_draws=40, band=30, seed=5, **kw):
    rng = random.Random(seed)
    out = []
    for e in events:
        s = sim_b(e, flat_min=e.sig.flat_min, **kw)
        if s is None:
            continue
        mins, bars, ex = dayx[(e.ticker, e.day)]
        stop_pct = (e.sig.entry - e.sig.stop) / e.sig.entry * 100
        cand = [j for j in range(len(bars) - 1) if ex[j][0] and abs(mins[j] + 1 - e.sig.elapsed) <= band and mins[j] + 1 != e.sig.elapsed and mins[j] + 1 >= 30]
        if len(cand) < 2:
            continue
        rs, ps = [], []
        for j in (rng.sample(cand, n_draws) if len(cand) > n_draws else cand):
            E = bars[j][4]
            S = E * (1 - stop_pct / 100)
            fut = [bars[i] + ex[i] for i in range(j + 1, len(bars))]
            r_ = sim_b(_BEv(E, S, fut), flat_min=385, **kw)
            if r_:
                rs.append(r_["r"]); ps.append(r_["ret_pct"])
        if rs:
            nr, npct = statistics.fmean(rs), statistics.fmean(ps)
            out.append({"day": e.day, "ticker": e.ticker, "f": e.feats, "r": s["r"], "ret_pct": s["ret_pct"], "a_r": s["r"] - nr, "a_ret": s["ret_pct"] - npct, "null_r": nr, "null_ret": npct})
    return out
