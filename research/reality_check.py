import random, statistics, math
from .common import SPLIT

def paired_stats(vals, base, keys, period):
    """paired per-event difference (variant - baseline) restricted to events filled in both; returns (mean, se_by_day_cluster, n)."""
    by = {}
    for v, b, (day, tk) in zip(vals, base, keys):
        if period == "D" and day > SPLIT["design_end"]: continue
        if period == "H" and day <= SPLIT["design_end"]: continue
        if v is None or b is None: continue
        s = by.setdefault(day, [0.0, 0]); s[0] += v - b; s[1] += 1
    n = sum(c for _, c in by.values())
    if n == 0: return 0.0, 1.0, 0
    m = sum(s for s, _ in by.values()) / n
    # cluster-robust se of the ratio estimator
    var = sum((s - m * c) ** 2 for s, c in by.values()) / (n ** 2)
    return m, math.sqrt(var) or 1e-9, n

def reality_check(variants, base, keys, period="D", reps=3000, seed=11):
    """White's Reality Check (day-block bootstrap). variants: list of per-event value lists (same event order as base).
    Returns observed max t-stat and p-value that the best variant's advantage over baseline is just selection luck."""
    days = sorted({d for d, _ in keys if (period == "D") == (d <= SPLIT["design_end"])})
    idx = {d: [i for i, (dd, _) in enumerate(keys) if dd == d] for d in days}
    diffs = []
    for vals in variants:
        diffs.append([(None if (v is None or b is None) else v - b) for v, b in zip(vals, base)])
    obs = []
    means = []
    for d in diffs:
        s = c = 0
        by = []
        for dd in days:
            ss = cc = 0
            for i in idx[dd]:
                if d[i] is not None: ss += d[i]; cc += 1
            by.append((ss, cc)); s += ss; c += cc
        m = s / c if c else 0.0
        means.append(m)
        var = sum((ss - m * cc) ** 2 for ss, cc in by) / (c ** 2) if c else 1
        obs.append(m / (math.sqrt(var) or 1e-9))
    tmax = max(obs)
    rng = random.Random(seed); cnt = 0
    perday = []
    for d in diffs:
        perday.append({dd: (sum(d[i] for i in idx[dd] if d[i] is not None), sum(1 for i in idx[dd] if d[i] is not None)) for dd in days})
    for _ in range(reps):
        sample = [days[rng.randrange(len(days))] for _ in days]
        best = -1e9
        for k, pd in enumerate(perday):
            s = sum(pd[dd][0] for dd in sample); c = sum(pd[dd][1] for dd in sample)
            if not c: continue
            mc = means[k]                       # recentre: null hypothesis = no advantage
            m = s / c - mc
            var = sum((pd[dd][0] - (s / c) * pd[dd][1]) ** 2 for dd in sample) / (c ** 2)
            best = max(best, m / (math.sqrt(var) or 1e-9))
        cnt += best >= tmax
    return tmax, cnt / reps
