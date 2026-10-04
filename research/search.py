"""Evaluation and search layer for the day-level formulas: periods, per-formula statistics with day-clustered confidence
intervals, a registry that counts every variant tried (for the multiple-testing correction), and White's reality check on
per-day return series.

Periods (fixed before any formula was looked at):
  D1     2026-01-02 .. 2026-05-29   tune here
  D2     2026-06-01 .. 2026-07-31   pick here
  FINAL  2026-08-03 .. 2026-10-02   locked: only the pre-registered shortlist is run on it, once
Symbols: the nine main ones are used for tuning; META and ABBV are kept as a symbol hold-out."""
import math
import random
import statistics
from collections import defaultdict
from datetime import date

from .daylab import run_events

D1 = (date(2026, 1, 2), date(2026, 5, 29))
D2 = (date(2026, 6, 1), date(2026, 7, 31))
FINAL = (date(2026, 8, 3), date(2026, 10, 2))
MAIN9 = ["KO", "MU", "IBM", "SPY", "AAPL", "MSFT", "NVDA", "QQQI", "TSLA"]
HOLDOUT_SYMS = ["META", "ABBV"]
MIN_N = 40


def in_period(recs, per):
    return [r for r in recs if per[0] <= r["day"] <= per[1]]


def pf_r(rs):
    pos, neg = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    return pos / neg if neg else 9.99


def cluster_stats(recs, key="acct_ret", reps=1000, seed=1):
    """Mean of `key` per trade with a day-clustered standard error and bootstrap CI; plus win rate, avg R, profit factor."""
    n = len(recs)
    if n == 0:
        return {"n": 0, "win": 0.0, "mean": 0.0, "r": 0.0, "pf": 0.0, "t": 0.0, "lo": 0.0, "hi": 0.0, "days": 0}
    by = defaultdict(lambda: [0.0, 0])
    for r in recs:
        by[r["day"]][0] += r[key]
        by[r["day"]][1] += 1
    days = list(by.values())
    m = sum(s for s, _ in days) / n
    var = sum((s - m * c) ** 2 for s, c in days) / (n * n)
    se = math.sqrt(var) or 1e-9
    lo = hi = m
    if len(days) >= 5:
        rng, ms = random.Random(seed), []
        for _ in range(reps):
            s_ = c_ = 0
            for _ in days:
                x = days[rng.randrange(len(days))]
                s_ += x[0]; c_ += x[1]
            ms.append(s_ / c_)
        ms.sort()
        lo, hi = ms[int(0.025 * reps)], ms[int(0.975 * reps) - 1]
    rs = [r["r"] for r in recs]
    return {"n": n, "win": sum(r["win"] for r in recs) / n * 100, "mean": m, "r": statistics.fmean(rs), "pf": pf_r(rs), "t": m / se,
            "lo": lo, "hi": hi, "days": len(days)}


def fmt(st, unit="%"):
    return (f"n={st['n']:4d} win={st['win']:5.1f}% ret/trade={st['mean']:+.3f}{unit} [{st['lo']:+.3f},{st['hi']:+.3f}] avgR={st['r']:+.3f} "
            f"PF={st['pf']:4.2f} t={st['t']:+.2f}")


class Registry:
    """Counts every variant evaluated and keeps its per-day return series so a reality check can be run over any subset."""

    def __init__(self):
        self.rows = []

    def add(self, family, name, params, recs_all):
        self.rows.append({"family": family, "name": name, "params": params, "recs": recs_all})
        return self.rows[-1]

    def count(self, family=None):
        return sum(1 for r in self.rows if family in (None, r["family"]))


def daily_series(recs, per, key="acct_ret"):
    """Sum of `key` per calendar trading day inside `per` (zero on days without a trade), as {day: value}."""
    out = defaultdict(float)
    for r in recs:
        if per[0] <= r["day"] <= per[1]:
            out[r["day"]] += r[key]
    return out


def reality_check_days(series_list, days, reps=2000, seed=11):
    """White's reality check on per-day series (one list per variant, aligned to `days`). Null: no variant has a positive mean.
    Returns (best studentised mean, p-value adjusted for choosing the best of all variants)."""
    n = len(days)
    stats = []
    for s in series_list:
        v = [s.get(d, 0.0) for d in days]
        m = sum(v) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1)) or 1e-9
        stats.append((v, m, sd))
    tobs = max(m / (sd / math.sqrt(n)) for _, m, sd in stats)
    rng, hits = random.Random(seed), 0
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        best = -1e9
        for v, m, sd in stats:
            mb = sum(v[i] for i in idx) / n
            best = max(best, (mb - m) / (sd / math.sqrt(n)))
        hits += best >= tobs
    return tobs, hits / reps


def evaluate(days, evs, **sim_kw):
    return run_events(days, evs, **sim_kw)
