"""Staged formula search for the four day-trading families, on DESIGN data only (Jan 2 - Jul 31, nine main symbols).

    python -m research.search_new GF          # also GG, RS, LH

The search never sees the locked FINAL period (Aug 3 - Oct 2) or the hold-out symbols: they are removed from the data before any
formula is generated. Selection rule (fixed in code, not adjusted after seeing results): among variants with at least MIN_N trades,
a positive mean and a positive mean in at least 3 of the 4 time blocks of the design period, take the highest day-clustered t-statistic
of the mean return per trade. Every variant evaluated is counted, and White's reality check is reported for each stage."""
import json
import sys
import time

from .families import GENERATORS, DEFAULTS, make_events
from .search import MAIN9, Registry, cluster_stats, daily_series, fmt, reality_check_days
from .common import *                                                      # noqa: F401,F403
from .daylab import build_days, run_events

DESIGN = (date(2026, 1, 2), date(2026, 7, 31))
MIN_N_BY_FAMILY = {"GF": 60, "GG": 40, "RS": 60, "LH": 100}      # set from how common each event type is, before looking at results
OUT = REPO / "data" / "research"


def design_days_for(universe=MAIN9, strict=True):
    data = load_bars(universe, DESIGN[0], DESIGN[1])
    n = {t: len(days_of(data[t])) for t in universe}
    if strict and (min(n.values()) < 0.95 * max(n.values()) or min(n.values()) < 100):
        raise SystemExit(f"incomplete history, finish the download first: {n}")
    return build_days(data, universe), data


def block_bounds(days):
    ds = sorted({d.day for d in days.values()})[1:]
    return [ds[int(len(ds) * k / 4)] for k in range(4)] + [ds[-1]]


def block_means(recs, bounds, key="acct_ret"):
    out = []
    for k in range(4):
        sub = [r[key] for r in recs if bounds[k] <= r["day"] < bounds[k + 1] or (k == 3 and r["day"] == bounds[4])]
        out.append(sum(sub) / len(sub) if sub else 0.0)
    return out


# ---------------------------------------------------------------------------------------------------------------------------
FILTERS = {
    "spy_up": lambda f: f["spy_ret"] >= 0.0, "spy_down": lambda f: f["spy_ret"] < -0.3, "spy_not_down": lambda f: f["spy_ret"] >= -0.3,
    "spy_above_vwap": lambda f: f["spy_above"] is True, "spy_below_vwap": lambda f: f["spy_above"] is False,
    "spy_gap_down": lambda f: f["spy_gap"] < -0.2, "spy_gap_not_down": lambda f: f["spy_gap"] >= -0.2,
    "rvol_ge1": lambda f: f["rvol"] >= 1.0, "rvol_lt1": lambda f: f["rvol"] < 1.0, "rvol_ge1.5": lambda f: f["rvol"] >= 1.5,
    "pm_ratio_ge2": lambda f: f["pm_ratio"] >= 2.0, "pm_ratio_lt1": lambda f: f["pm_ratio"] < 1.0,
    "prev_up": lambda f: f["prev_ret1"] > 0, "prev_down": lambda f: f["prev_ret1"] < 0, "prev5_up": lambda f: f["prev_ret5"] > 0,
    "above_vwap": lambda f: f["above_vwap"] is True, "below_vwap": lambda f: f["above_vwap"] is False,
    "ema_up": lambda f: f["ema_order"] is True, "ema_down": lambda f: f["ema_order"] is False,
    "near_lod": lambda f: f["lod_dist"] <= 0.2, "far_lod": lambda f: f["lod_dist"] >= 0.4,
    "near_hod": lambda f: f["hod_dist"] <= 0.2, "far_hod": lambda f: f["hod_dist"] >= 0.5,
    "gap_small_atr": lambda f: abs(f["gap_atr"]) <= 0.7, "gap_large_atr": lambda f: abs(f["gap_atr"]) >= 0.7,
    "atr_low": lambda f: f["atr5_pct"] <= 0.12, "atr_high": lambda f: f["atr5_pct"] >= 0.16,
    "range_small": lambda f: f["range_atr"] <= 0.8, "range_large": lambda f: f["range_atr"] >= 1.0,
    "above_prior_low": lambda f: f["dist_prior_low"] >= 0, "below_prior_low": lambda f: f["dist_prior_low"] < 0,
    "above_prior_high": lambda f: f["dist_prior_high"] >= 0, "below_prior_high": lambda f: f["dist_prior_high"] < 0,
    "rs_pos": lambda f: f["rs"] >= 0, "rs_neg": lambda f: f["rs"] < 0, "rs_ge0.5": lambda f: f["rs"] >= 0.5,
    "ret_open_pos": lambda f: f["ret_open"] >= 0, "ret_open_neg": lambda f: f["ret_open"] < 0,
    "early": lambda f: f["i"] <= 30, "late": lambda f: f["i"] > 30,
    "prev_strong": lambda f: f["prev_close_pos"] >= 0.7, "prev_weak": lambda f: f["prev_close_pos"] <= 0.3,
    "gap_inside_prior": lambda f: f["gap_inside"] is True, "gap_breakaway": lambda f: f["gap_inside"] is False,
    "near_20d_low": lambda f: f["dist_lo20"] <= 3.0, "far_20d_low": lambda f: f["dist_lo20"] >= 8.0,
    "near_20d_high": lambda f: f["dist_hi20"] >= -3.0, "far_20d_high": lambda f: f["dist_hi20"] <= -8.0,
    "volatile_stock": lambda f: f["atr_d_pct"] >= 2.0, "calm_stock": lambda f: f["atr_d_pct"] <= 1.5,
}


def apply_filter(evs, names):
    return [e for e in evs if all(FILTERS[n](e.f) for n in names)]


# ---------------------------------------------------------------------------------------------------------------------------
class Search:
    def __init__(self, family, days):
        self.family, self.days, self.reg = family, days, Registry()
        self.bounds = block_bounds(days)
        self.cur_params = None
        self.design_dates = sorted({d.day for d in days.values()})[1:]
        self.log = []

    def score(self, name, evs, params, formula=None):
        recs = run_events(self.days, evs)
        st = cluster_stats(recs)
        bm = block_means(recs, self.bounds)
        row = {"name": name, "params": params, "formula": formula, "st": st, "blocks": bm, "pos_blocks": sum(b > 0 for b in bm), "recs": recs}
        self.reg.add(self.family, name, params, recs)
        self.reg.rows[-1].update(st=st, blocks=bm, pos_blocks=row["pos_blocks"], formula=formula)
        return row

    def stage(self, title, variants, base_events=None, top=8):
        """variants: list of (name, params) for generator variants, or (name, filter-names) when base_events is given."""
        rows = []
        for name, p in variants:
            if base_events is not None:
                evs, formula = apply_filter(base_events, p), {"params": self.cur_params, "filters": list(p)}
            else:
                evs, formula = make_events(self.days, MAIN9, self.family, **p), {"params": p, "filters": []}
            rows.append(self.score(name, evs, p, formula))
        min_n = MIN_N_BY_FAMILY[self.family]
        ok = [r for r in rows if r["st"]["n"] >= min_n and r["st"]["mean"] > 0 and r["pos_blocks"] >= 3]
        ok.sort(key=lambda r: -r["st"]["t"])
        series = [daily_series(r["recs"], DESIGN) for r in rows if r["st"]["n"] >= min_n]
        tobs, p_rc = reality_check_days(series, self.design_dates, reps=1000) if series else (0.0, 1.0)
        print(f"\n--- {self.family} / {title}: {len(rows)} variants ({self.reg.count(self.family)} so far); reality check on this stage: best t={tobs:.2f}, p={p_rc:.3f}")
        for r in (ok or sorted(rows, key=lambda r: -r["st"]["t"]))[:top]:
            tag = "" if r in ok else "  (fails the selection rule)"
            print(f"   {r['name']:<46} {fmt(r['st'])} blocks+ {r['pos_blocks']}/4{tag}")
        self.log.append({"stage": title, "variants": len(rows), "rc_p": p_rc, "best": ok[0]["name"] if ok else None})
        return ok[0] if ok else None


def grid(base, **axes):
    """Cartesian product of named axes; each value may be a plain value or a dict of several parameter overrides."""
    import itertools
    keys = list(axes)
    out = []
    for combo in itertools.product(*[axes[k] for k in keys]):
        p, parts = dict(base), []
        for k, v in zip(keys, combo):
            if isinstance(v, dict):
                p.update(v); parts.append(f"{k}={v.get('_n', v)}")
            else:
                p[k] = v; parts.append(f"{k}={v}")
        p.pop("_n", None)
        out.append((" ".join(parts), p))
    return out


def run_family(fam, days):
    S = Search(fam, days)
    base = dict(DEFAULTS[fam])
    best = None
    if fam == "GF":
        v = [(n, p) for n, p in grid(base, g_min=[0.3, 0.5, 0.75, 1.0, 1.5, 2.0], g_max=[1.5, 2.5, 4.0, 99.0]) if p["g_max"] > p["g_min"] + 0.3]
        best = S.stage("1: gap size band", v)
        if not best: return S, None
        b = best["params"]
        v = grid(b, t_start=[5, 15, 30, 60], confirm=["none", "orh", "vwap", "ema9", "green5", "hl", "green5_vwap"])
        v = [(n, {**p, "t_end": p["t_start"] + 75}) for n, p in v]
        best = S.stage("2: entry timing and confirmation", v)
        if not best: return S, best
        b = best["params"]
        tg = [{"_n": "fill_all", "target": "fill_all"}, {"_n": "split.5", "target": "fill_split", "a": 0.5}, {"_n": "split.33", "target": "fill_split", "a": 0.33},
              {"_n": "R1/2", "target": "r", "t1_r": 1.0, "t2_r": 2.0}, {"_n": "R1.5/2.5", "target": "r", "t1_r": 1.5, "t2_r": 2.5}]
        st_ = [{"_n": "lod", "stop": "lod"}, {"_n": "atr1.5", "stop": "atr", "atr_k": 1.5}, {"_n": "atr2.5", "stop": "atr", "atr_k": 2.5}, {"_n": "atr3.5", "stop": "atr", "atr_k": 3.5}]
        best = S.stage("3: stop, target, exit time", grid(b, stop=st_, target=tg, flat=[120, 240, 385]))
    elif fam == "GG":
        v = grid(base, g_min=[0.5, 1.0, 1.5, 2.0, 3.0], trigger=["orh5", "orh15", "pmh", "vwap_bounce", "none"], pm_ratio_min=[0.0, 1.5, 3.0])
        best = S.stage("1: gap size, trigger, premarket volume", v)
        if not best: return S, None
        b = best["params"]
        stops = [{"_n": "orl", "stop": "orl"}, {"_n": "vwap", "stop": "vwap"}, {"_n": "atr1.5", "stop": "atr", "atr_k": 1.5}, {"_n": "atr2.5", "stop": "atr", "atr_k": 2.5}]
        best = S.stage("2: stop, targets, exit", grid(b, stop=stops, t1_r=[1.0, 1.5, 2.0], t2_r=[0, 3.0], exit_mode=[None, "vwap", "ema20"], flat=[240, 385]))
    elif fam == "RS":
        v = grid(base, side=["leader", "laggard"], t_dec=[30, 60, 90, 120], rs_min=[0.3, 0.5, 1.0, 1.5], spy_state=["any", "down", "up"])
        best = S.stage("1: side, decision time, RS threshold, SPY state", v)
        if not best: return S, None
        b = best["params"]
        best = S.stage("2: trigger, cross-sectional rank, VWAP", grid(b, trigger=["now", "pullback", "break30"], rank_max=[0, 1, 2, 3], need_vwap=[True, False]))
        if not best: return S, best
        b = best["params"]
        stops = [{"_n": "vwap", "stop": "vwap"}, {"_n": "lowN", "stop": "lowN"}, {"_n": "atr2", "stop": "atr", "atr_k": 2.0}, {"_n": "atr3", "stop": "atr", "atr_k": 3.0}]
        best = S.stage("3: stop, targets, time stop, exit", grid(b, stop=stops, t1_r=[1.0, 1.5, 2.0], ts_min=[60, 90, 180, None], exit_mode=[None, "vwap", "ema20"]))
    elif fam == "LH":
        best = S.stage("1: entry time, no signal", grid(base, t_ent=[270, 300, 330, 360]))
        singles = {"ret_open>=0": dict(need_ret_open=0.0), "ret_open>=0.3": dict(need_ret_open=0.3), "ret_open>=0.6": dict(need_ret_open=0.6),
                   "ret_open>=1.0": dict(need_ret_open=1.0), "first30>=0": dict(need_first30=0.0), "above_vwap": dict(need_vwap=True),
                   "below_vwap": dict(need_vwap=False), "near_hod<=0.1": dict(near_hod=0.1), "near_hod<=0.3": dict(near_hod=0.3),
                   "near_hod<=0.6": dict(near_hod=0.6), "spy_up": dict(spy_up=True), "spy_down": dict(spy_up=False), "rs>=0": dict(rs_min=0.0),
                   "rs>=0.3": dict(rs_min=0.3), "rs>=0.6": dict(rs_min=0.6), "oversold>=1": dict(oversold=1.0), "oversold>=2": dict(oversold=2.0)}
        v = [(f"t_ent={t} {n}", {**base, "t_ent": t, **kw}) for t in (270, 300, 330, 360) for n, kw in singles.items()]
        best = S.stage("2: single signals at each entry time", v, top=10)
        if not best: return S, None
        b = best["params"]
        # greedy second condition on top of the best single
        v = [(f"+ {n}", {**b, **kw}) for n, kw in singles.items() if all(b.get(k) != vv for k, vv in kw.items())]
        best = S.stage("3: add a second condition", v, top=8) or best
        b = best["params"]
        stops = [{"_n": f"atr{k}", "stop": "atr", "atr_k": k} for k in (2.0, 3.0, 4.0)] + [{"_n": f"pct{x}", "stop": "pct", "stop_pct": x} for x in (0.3, 0.5, 0.8)]
        best = S.stage("4: stop and first target", grid(b, stop=stops, t1_r=[0.0, 1.0, 1.5]))
    if not best: return S, None
    # extra signals: greedy forward selection of filters on the final events
    cur_p, chosen = best["params"], []
    S.cur_params = cur_p
    evs = make_events(S.days, MAIN9, fam, **cur_p)
    cur = best
    for step in range(3):
        cand = [(f"{'+'.join(chosen + [n])}", chosen + [n]) for n in FILTERS if n not in chosen]
        r = S.stage(f"5.{step + 1}: add an extra signal (current: {'+'.join(chosen) or 'none'}; base t={cur['st']['t']:+.2f})", cand, base_events=evs, top=6)
        if r is None or r["st"]["t"] <= cur["st"]["t"]:
            break
        chosen, cur = r["params"], r
    best_final = {"params": cur_p, "filters": chosen, "stats": {k: v for k, v in cur["st"].items()}, "blocks": cur["blocks"]}
    # the win-rate formula: highest win rate among everything tried that still has a positive, reasonably significant, block-consistent mean
    mn = MIN_N_BY_FAMILY[fam]
    pool = [r for r in S.reg.rows if r["st"]["n"] >= mn and r["st"]["mean"] > 0 and r["st"]["t"] >= 1.5 and r["pos_blocks"] >= 3 and r["formula"]]
    if pool:
        w = max(pool, key=lambda r: (round(r["st"]["win"], 1), r["st"]["t"]))
        best_final["win_formula"] = {"params": w["formula"]["params"], "filters": w["formula"]["filters"], "stats": dict(w["st"]), "blocks": w["blocks"], "name": w["name"]}
        print(f"\n[{fam}] highest win rate with t>=1.5 and 3/4 blocks positive: {w['name']}  {fmt(w['st'])}")
    # one reality check over EVERY variant tried for this family (the per-stage checks understate the size of the search)
    rows = [r for r in S.reg.rows if len(r["recs"]) >= MIN_N_BY_FAMILY[fam]]
    tobs, p_all = reality_check_days([daily_series(r["recs"], DESIGN) for r in rows], S.design_dates, reps=1000)
    print(f"\n[{fam}] overall reality check across all {len(rows)} variants with enough trades: best t={tobs:.2f}, adjusted p={p_all:.3f}")
    best_final["rc_all"] = {"variants": S.reg.count(fam), "best_t": tobs, "p": p_all}
    return S, best_final


def main():
    fam = sys.argv[1].upper()
    t0 = time.time()
    days, _ = design_days_for()
    print(f"design data: {len(days)} symbol-days, blocks start {[str(b) for b in block_bounds(days)]}")
    S, best = run_family(fam, days)
    print(f"\n=== {fam}: {S.reg.count(fam)} variants evaluated in {time.time() - t0:.0f}s ===")
    if best:
        print("FINAL FORMULA (design data):", json.dumps({k: v for k, v in best.items() if k != 'blocks'}, default=str))
        print("   block means (acct ret per trade, %):", [round(x, 3) for x in best["blocks"]])
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"search_{fam}.json").write_text(json.dumps({"best": best, "stages": S.log, "variants": S.reg.count(fam)}, default=str, indent=1))
    else:
        print("no variant passed the selection rule")


if __name__ == "__main__":
    main()
