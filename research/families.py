"""Event generators for four day-trading families, long-only, on top of research.daylab:

  GF  gap fill      buy a stock that gapped DOWN and bet it fills toward the prior close
  GG  gap and go    buy a stock that gapped UP with strength and ride the continuation
  RS  relative strength vs SPY (and cross-sectional): buy the leaders (or the laggards) of the morning
  LH  last-hour     buy into the close when the day's picture is bullish (or oversold), exit at 15:55

Each generator returns `Ev` candidates (signal at the close of minute i, limit order for the next bar) with a feature dict that the
search uses to test extra signals. Parameters live in plain dicts so a formula is just {"family": ..., **params}."""
from .daylab import *                                                      # noqa: F401,F403

MAX_STOP_PCT = 1.5          # no trade is taken with a stop wider than this (percent of price)


def nan(x):
    return x != x


# ---------------------------------------------------------------------------------------------------------------------------
# shared context features
def spy_of(days, day):
    return days.get(("SPY", day))


def feats(days, d, i):
    """Everything an extra signal might want to know at the close of minute i (no look-ahead)."""
    c, atr5, s = d.c[i], d.atr5[i], spy_of(days, d.day)
    has_spy = s is not None and i < s.n and d.t != "SPY"
    sr = s.ret_open(i) if has_spy else NAN
    ro = d.ret_open(i)
    f = {"i": i, "gap": d.gap_pct, "ret_open": ro, "spy_ret": sr, "rs": ro - sr if has_spy else NAN,
         "spy_above": (s.c[i] > s.vwap[i]) if has_spy else None, "spy_gap": s.gap_pct if has_spy else NAN,
         "above_vwap": c > d.vwap[i], "rvol": d.rvol(i), "prev_ret1": d.prev_ret1, "prev_ret5": d.prev_ret5,
         "hod_dist": (d.hod[i] - c) / c * 100, "lod_dist": (c - d.lod[i]) / c * 100, "atr5_pct": atr5 / c * 100 if not nan(atr5) else NAN}
    f["gap_atr"] = (d.o[0] - d.prior_close) / d.atr_d if (d.prior_close and not nan(d.atr_d) and d.atr_d > 0) else NAN
    f["pm_ratio"] = d.pm_vol / d.avg_pm_vol if (not nan(d.avg_pm_vol) and d.avg_pm_vol > 0) else NAN
    f["dist_vwap_atr"] = (c - d.vwap[i]) / atr5 if (not nan(atr5) and atr5 > 0) else NAN
    f["ema_order"] = (d.ema9[i] > d.ema20[i]) if not (nan(d.ema9[i]) or nan(d.ema20[i])) else None
    f["range_atr"] = (d.hod[i] - d.lod[i]) / d.atr_d if (not nan(d.atr_d) and d.atr_d > 0) else NAN
    f["dist_prior_high"] = (c / d.prior_high - 1) * 100 if d.prior_high else NAN
    f["dist_prior_low"] = (c / d.prior_low - 1) * 100 if d.prior_low else NAN
    f["atr_d_pct"] = d.atr_d / c * 100 if not nan(d.atr_d) else NAN
    rng_p = (d.prior_high - d.prior_low) if d.prior_high is not None else None
    f["prev_close_pos"] = (d.prior_close - d.prior_low) / rng_p if rng_p and rng_p > 0 else NAN       # where yesterday closed in its range
    f["gap_inside"] = (d.prior_low <= d.o[0] <= d.prior_high) if rng_p is not None else None           # opened inside yesterday's range?
    f["dist_lo20"] = (c / d.lo20 - 1) * 100 if not nan(d.lo20) else NAN
    f["dist_hi20"] = (c / d.hi20 - 1) * 100 if not nan(d.hi20) else NAN
    return f


def _mk(d, i, S, T1, T2, kw, f, extra=None):
    E = round(d.c[i] + 0.02, 4)
    R = E - S
    if R <= 0 or R / E * 100 > MAX_STOP_PCT:
        return None
    return Ev(d.t, d.day, i, E, round(S, 4), T1, T2, kw, {**f, **(extra or {}), "stop_pct": R / E * 100})


# ---------------------------------------------------------------------------------------------------------------------------
# GF: gap fill (buy gap-downs)
GF_DEFAULT = dict(g_min=0.5, g_max=3.0, t_start=15, t_end=90, confirm="none", stop="lod", atr_k=2.0, target="fill_split",
                  a=0.5, t1_r=1.5, t2_r=2.5, rr_min=0.0, flat=385, ts_min=None, exit_mode=None)


def confirm_ok(d, i, mode):
    if mode == "none":
        return True
    if mode == "orh":                                   # close above the first-5-minute high
        return i >= 5 and d.c[i] > max(d.h[:5])
    if mode == "vwap":                                  # close above session VWAP
        return d.c[i] > d.vwap[i]
    if mode == "ema9":                                  # a 5-minute close above the 5-minute EMA9
        return d.new5[i] == 1.0 and not nan(d.ema9[i]) and d.c5[i] > d.ema9[i]
    if mode == "green5":                                # the 5-minute bar that just closed was green
        return d.new5[i] == 1.0 and i >= 4 and d.c[i] > d.o[i - 4]
    if mode == "hl":                                    # three rising 1-minute lows
        return i >= 3 and d.l[i] > d.l[i - 1] > d.l[i - 2]
    if mode == "green5_vwap":
        return d.new5[i] == 1.0 and i >= 4 and d.c[i] > d.o[i - 4] and d.c[i] > d.vwap[i]
    raise ValueError(mode)


def _stop(d, i, mode, k):
    atr5 = d.atr5[i] if not nan(d.atr5[i]) else 0.02
    E = d.c[i] + 0.02
    if mode == "lod":
        return d.lod[i] - 0.1 * atr5
    if mode == "orl":
        return min(d.l[:max(i, 5) + 1][:15]) - 0.1 * atr5
    if mode == "atr":
        return E - k * atr5
    raise ValueError(mode)


def gf_events(days, universe, **kw):
    p = {**GF_DEFAULT, **kw}
    out = []
    for (t, day), d in days.items():
        if t not in universe or not d.full or nan(d.gap_pct) or not (-p["g_max"] <= d.gap_pct <= -p["g_min"]):
            continue
        hi = min(p["t_end"], d.n - 2)
        i = next((j for j in range(p["t_start"], hi + 1) if confirm_ok(d, j, p["confirm"])), None)
        if i is None or nan(d.atr5[i]):
            continue
        E = d.c[i] + 0.02
        gap_dist = d.prior_close - E
        if gap_dist <= 0:
            continue
        S = _stop(d, i, p["stop"], p["atr_k"])
        R = E - S
        if R <= 0 or gap_dist / R < p["rr_min"]:
            continue
        if p["target"] == "fill_all":
            T1, T2, frac = d.prior_close, None, 1.0
        elif p["target"] == "fill_split":
            T1, T2, frac = E + p["a"] * gap_dist, d.prior_close, 0.5
        elif p["target"] == "r":
            T1, T2, frac = E + p["t1_r"] * R, E + p["t2_r"] * R, 0.5
        else:
            raise ValueError(p["target"])
        ev = _mk(d, i, S, T1, T2, dict(t1_frac=frac, ts_min=p["ts_min"], flat_min=p["flat"], exit_mode=p["exit_mode"]), feats(days, d, i),
                 {"gap_dist_r": gap_dist / R})
        if ev:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# GG: gap and go (buy strong gap-ups)
GG_DEFAULT = dict(g_min=1.0, g_max=99.0, pm_ratio_min=0.0, trigger="orh5", t_start=5, t_end=60, stop="orl", atr_k=2.0, t1_r=1.5, t2_r=3.0,
                  frac=0.5, flat=385, ts_min=None, exit_mode=None)


def gg_trigger(d, i, mode):
    if mode == "orh5":
        return i >= 5 and d.c[i] > max(d.h[:5])
    if mode == "orh15":
        return i >= 15 and d.c[i] > max(d.h[:15])
    if mode == "pmh":
        return d.pm_high is not None and d.c[i] > d.pm_high and i >= 1
    if mode == "vwap_bounce":                           # price pulled back to VWAP earlier and is now back above it with a green bar
        return i >= 6 and d.c[i] > d.vwap[i] and min(d.l[i - 5:i]) <= d.vwap[i] * 1.0005 and d.c[i] > d.o[i]
    if mode == "none":
        return True
    raise ValueError(mode)


def gg_events(days, universe, **kw):
    p = {**GG_DEFAULT, **kw}
    out = []
    for (t, day), d in days.items():
        if t not in universe or not d.full or nan(d.gap_pct) or not (p["g_min"] <= d.gap_pct <= p["g_max"]):
            continue
        if p["pm_ratio_min"] and (nan(d.avg_pm_vol) or d.avg_pm_vol <= 0 or d.pm_vol / d.avg_pm_vol < p["pm_ratio_min"]):
            continue
        hi = min(p["t_end"], d.n - 2)
        i = next((j for j in range(p["t_start"], hi + 1) if gg_trigger(d, j, p["trigger"]) and d.c[j] > d.vwap[j]), None)
        if i is None or nan(d.atr5[i]):
            continue
        E = d.c[i] + 0.02
        if p["stop"] == "orl":
            S = min(d.l[:15]) - 0.1 * d.atr5[i] if i >= 15 else min(d.l[:i + 1]) - 0.1 * d.atr5[i]
        elif p["stop"] == "vwap":
            S = min(d.vwap[i], min(d.l[max(0, i - 4):i + 1])) - 0.1 * d.atr5[i]
        else:
            S = E - p["atr_k"] * d.atr5[i]
        R = E - S
        if R <= 0:
            continue
        ev = _mk(d, i, S, E + p["t1_r"] * R, (E + p["t2_r"] * R) if p["t2_r"] else None,
                 dict(t1_frac=p["frac"], ts_min=p["ts_min"], flat_min=p["flat"], exit_mode=p["exit_mode"]), feats(days, d, i))
        if ev:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# RS: relative strength vs SPY, plus cross-sectional rank
RS_DEFAULT = dict(side="leader", t_dec=60, rs_min=0.5, need_vwap=True, spy_state="any", trigger="now", t_end=150, stop="vwap", atr_k=2.0,
                  t1_r=1.5, t2_r=2.5, frac=0.5, flat=385, ts_min=90, exit_mode=None, rank_max=0)

_xs_cache: dict = {}


def xs_rank(days, universe, day, i):
    """{ticker: rank of its return since the open at minute i, 1 = strongest} over the universe symbols with data that day."""
    key = (id(days), tuple(sorted(universe)), day, i)
    if key not in _xs_cache:
        rets = {t: days[(t, day)].ret_open(i) for t in universe if (t, day) in days and days[(t, day)].full and i < days[(t, day)].n}
        order = sorted(rets, key=lambda t: -rets[t])
        _xs_cache[key] = {t: r + 1 for r, t in enumerate(order)}
    return _xs_cache[key]


def rs_trigger_ok(d, i, mode):
    if mode == "now":
        return True
    if mode == "pullback":                              # touched EMA9/VWAP area in the last 6 minutes and closed green back above both
        if i < 6 or nan(d.ema9[i]):
            return False
        near = min(d.l[i - 5:i + 1]) <= max(d.ema9[i], d.vwap[i]) * 1.0003
        return near and d.c[i] > d.o[i] and d.c[i] > d.vwap[i] and d.c[i] > d.ema9[i]
    if mode == "break30":                               # close above the highest high of the last 30 minutes
        return i >= 31 and d.c[i] > max(d.h[i - 30:i])
    raise ValueError(mode)


def rs_events(days, universe, **kw):
    p = {**RS_DEFAULT, **kw}
    out = []
    for (t, day), d in days.items():
        if t not in universe or t == "SPY" or not d.full or d.prior_close is None:
            continue
        s = spy_of(days, day)
        if s is None or p["t_dec"] >= d.n - 2:
            continue
        i0 = p["t_dec"]
        hi = min(p["t_end"], d.n - 2)
        chosen = None
        for i in range(i0, hi + 1):
            if i >= s.n or nan(d.atr5[i]):
                continue
            sr, ro = s.ret_open(i), d.ret_open(i)
            rs = ro - sr
            if p["side"] == "leader":
                ok = rs >= p["rs_min"] and (not p["need_vwap"] or d.c[i] > d.vwap[i])
            else:                                       # laggard: weak versus SPY and oversold, then a bounce bar
                ok = rs <= -p["rs_min"] and d.c[i] > d.o[i] and d.c[i] > d.c[i - 1]
            if not ok:
                continue
            if p["spy_state"] == "down" and not (sr <= -0.2):
                continue
            if p["spy_state"] == "up" and not (sr >= 0.2):
                continue
            if p["spy_state"] == "not_down" and sr < -0.2:
                continue
            if p["rank_max"]:
                rk = xs_rank(days, universe - {"SPY"} if isinstance(universe, set) else set(universe) - {"SPY"}, day, i).get(t)
                if rk is None or rk > p["rank_max"]:
                    continue
            if not rs_trigger_ok(d, i, p["trigger"]):
                continue
            chosen = i
            break
        if chosen is None:
            continue
        i = chosen
        E = d.c[i] + 0.02
        if p["stop"] == "vwap":
            S = min(d.vwap[i], min(d.l[max(0, i - 9):i + 1])) - 0.1 * d.atr5[i]
        elif p["stop"] == "lowN":
            S = min(d.l[max(0, i - 14):i + 1]) - 0.1 * d.atr5[i]
        else:
            S = E - p["atr_k"] * d.atr5[i]
        R = E - S
        if R <= 0:
            continue
        ev = _mk(d, i, S, E + p["t1_r"] * R, (E + p["t2_r"] * R) if p["t2_r"] else None,
                 dict(t1_frac=p["frac"], ts_min=p["ts_min"], flat_min=p["flat"], exit_mode=p["exit_mode"]), feats(days, d, i))
        if ev:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# LH: last-hour momentum (or oversold bounce) into the close
LH_DEFAULT = dict(t_ent=330, stop="atr", atr_k=3.0, stop_pct=0.5, t1_r=0.0, frac=0.5, flat=385, ts_min=None, exit_mode=None,
                  need_ret_open=None, need_first30=None, need_vwap=None, near_hod=None, spy_up=None, rs_min=None, oversold=None)


def lh_signal(d, i, s, p):
    """True when every requested condition holds at the close of minute i."""
    if p["need_ret_open"] is not None and not (d.ret_open(i) >= p["need_ret_open"]):
        return False
    if p["need_first30"] is not None and not ((d.c[29] / d.o[0] - 1) * 100 >= p["need_first30"]):
        return False
    if p["need_vwap"] is True and not d.c[i] > d.vwap[i]:
        return False
    if p["need_vwap"] is False and not d.c[i] <= d.vwap[i]:
        return False
    if p["near_hod"] is not None and not ((d.hod[i] - d.c[i]) / d.c[i] * 100 <= p["near_hod"]):
        return False
    if p["spy_up"] is not None:
        if s is None or i >= s.n:
            return False
        if p["spy_up"] is True and not s.ret_open(i) >= 0:
            return False
        if p["spy_up"] is False and not s.ret_open(i) < 0:
            return False
    if p["rs_min"] is not None:
        if s is None or i >= s.n or d.t == "SPY" or not (d.ret_open(i) - s.ret_open(i) >= p["rs_min"]):
            return False
    if p["oversold"] is not None:                       # distance below VWAP in ATR(5m) units at least this deep
        if nan(d.atr5[i]) or not ((d.vwap[i] - d.c[i]) / d.atr5[i] >= p["oversold"]):
            return False
    return True


def lh_events(days, universe, **kw):
    p = {**LH_DEFAULT, **kw}
    out = []
    for (t, day), d in days.items():
        if t not in universe or not d.full or d.prior_close is None:
            continue
        i = p["t_ent"] - 1
        if i >= d.n - 2 or nan(d.atr5[i]):
            continue
        s = spy_of(days, day)
        if not lh_signal(d, i, s, p):
            continue
        E = d.c[i] + 0.02
        S = E - p["atr_k"] * d.atr5[i] if p["stop"] == "atr" else E * (1 - p["stop_pct"] / 100)
        R = E - S
        T1 = E + p["t1_r"] * R if p["t1_r"] else None
        ev = _mk(d, i, S, T1, None, dict(t1_frac=p["frac"] if T1 else 0.5, ts_min=p["ts_min"], flat_min=p["flat"], exit_mode=p["exit_mode"]), feats(days, d, i))
        if ev:
            out.append(ev)
    return out


GENERATORS = {"GF": gf_events, "GG": gg_events, "RS": rs_events, "LH": lh_events}
DEFAULTS = {"GF": GF_DEFAULT, "GG": GG_DEFAULT, "RS": RS_DEFAULT, "LH": LH_DEFAULT}


def make_events(days, universe, family, **params):
    return GENERATORS[family](days, set(universe), **params)
