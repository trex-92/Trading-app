"""Pure-python ridge regression + OOS check: do the captured features predict trade R out of sample?"""
import math, random, statistics
from .scalp_lab import *

def solve(A, b):
    n = len(A); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c])); M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        for j in range(c, n + 1): M[c][j] /= pv
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]
                for j in range(c, n + 1): M[r][j] -= f * M[c][j]
    return [M[i][n] for i in range(n)]

NUM = ["elapsed", "stop_pct", "r_over_atr1", "atr1_pct", "atr5_pct", "ema_gap_pct", "vwap_slope_pct", "dist_vwap_pct", "dist_vwap_R",
       "trig_vol_ratio", "pb_n", "pb_age", "n_block", "near_level_R", "hod_R", "gap_pct", "ret_open_pct", "or_range_pct", "spy_ret_open_pct",
       "price", "cost_over_R", "fl_crosses"]
BOOL = ["fl_close_above_vwap", "fl_vwap_rising", "fl_ema_order", "fl_emas_rising", "fl_or_break", "above_or_high",
        "spy_above_vwap", "spy_vwap_rising", "spy_ema_order"]

def raw(r):
    f = dict(r["f"]); f["cost_over_R"] = 0.02 / (r["ev"].sig.entry - r["ev"].sig.stop)
    f["near_level_R"] = 5.0 if f.get("near_level_R") is None else min(f["near_level_R"], 5.0)
    f["hod_R"] = min(f.get("hod_R", 10), 10.0); f["price"] = math.log(f["price"])
    return [(f.get(k) if f.get(k) is not None else 0.0) for k in NUM] + [1.0 if f.get(k) else 0.0 for k in BOOL]

class Model:
    def fit(self, rows, y, lam):
        X = [raw(r) for r in rows]; d = len(X[0])
        cols = list(zip(*X)); self.lo = []; self.hi = []
        for c in cols:
            s = sorted(c); self.lo.append(s[int(len(s) * .01)]); self.hi.append(s[int(len(s) * .99)])
        X = [self._clip(x) for x in X]; cols = list(zip(*X))
        self.mu = [statistics.fmean(c) for c in cols]; self.sd = [statistics.pstdev(c) or 1.0 for c in cols]
        Z = [[(x[j] - self.mu[j]) / self.sd[j] for j in range(d)] for x in X]
        self.ym = statistics.fmean(y); yc = [v - self.ym for v in y]
        A = [[sum(z[i] * z[j] for z in Z) + (lam if i == j else 0.0) for j in range(d)] for i in range(d)]
        b = [sum(z[i] * v for z, v in zip(Z, yc)) for i in range(d)]
        self.beta = solve(A, b); return self
    def _clip(self, x): return [min(max(v, self.lo[j]), self.hi[j]) for j, v in enumerate(x)]
    def predict(self, rows):
        out = []
        for r in rows:
            x = self._clip(raw(r)); out.append(self.ym + sum(self.beta[j] * (x[j] - self.mu[j]) / self.sd[j] for j in range(len(x))))
        return out

def corr(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else float("nan")

def rank(a):
    idx = sorted(range(len(a)), key=lambda i: a[i]); r = [0] * len(a)
    for k, i in enumerate(idx): r[i] = k
    return r
