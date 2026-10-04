"""Final out-of-sample evaluation of the pre-registered day-trading formulas (research/PREREGISTRATION_2.md, research/new_formulas.json).

    python -m research.final_new                 # runs once on the locked FINAL period and the symbol hold-out
    python -m research.final_new --dry-run       # the same code on DESIGN data, only to check it runs (verdicts mean nothing)

Per formula it reports: design-period result (9 main symbols), the same on the two hold-out symbols, the locked FINAL period (all 11
symbols), the engine view of FINAL with random symbol orderings, and a neighbourhood table (nearby parameter values) to show whether
the result is a smooth region or a spike."""
import argparse
import hashlib
import json

from .common import *                                                      # noqa: F401,F403
from .daylab import Replay, build_days, run_events
from .families import make_events
from .search import FINAL, HOLDOUT_SYMS, MAIN9, cluster_stats, fmt
from .search_new import DESIGN, FILTERS, apply_filter

FORMULAS = REPO / "research" / "new_formulas.json"
ALL11 = MAIN9 + HOLDOUT_SYMS


def formula_events(days, universe, f):
    return apply_filter(make_events(days, universe, f["family"], **f["params"]), f.get("filters", []))


def view(days, universe, f, period, label):
    recs = [r for r in run_events(days, formula_events(days, universe, f)) if period[0] <= r["day"] <= period[1]]
    st = cluster_stats(recs)
    print(f"   {label:<34} {fmt(st)}")
    return recs, st


def engine_view(data, days, universe, f, period, orders=30):
    evs = [e for e in formula_events(days, universe, f) if period[0] <= e.day <= period[1]]
    rng, rows = random.Random(1), []
    for _ in range(orders):
        order = list(universe)
        rng.shuffle(order)
        lo = period[0] - timedelta(days=7)                 # a few earlier days so the engine's warm-up day is not inside the period
        res = engine(lambda: Replay(evs), order, {t: [b for b in data[t] if lo <= b.ts.astimezone(US.tz).date() <= period[1]] for t in order})
        rs = [t["r"] for t in res.trades]
        pos, neg = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
        rows.append((len(rs), statistics.fmean(rs) if rs else 0.0, pos / neg if neg else 9.99, res.stats["return_pct"],
                     sum(x > 0 for x in rs) / len(rs) * 100 if rs else 0.0))
    m = lambda k: statistics.fmean(r[k] for r in rows)
    print(f"   engine, mean of {orders} orderings: trades {m(0):.0f} | win {m(4):.1f}% | avg R {m(1):+.3f} | PF {m(2):.2f} | return {m(3):+.2f}% "
          f"(orderings {min(r[3] for r in rows):+.2f}..{max(r[3] for r in rows):+.2f})")
    return rows


def neighbours(f):
    """Parameter variants one grid step away from the formula, for the smoothness check."""
    p, out = f["params"], []
    steps = {"t_ent": [-30, 30], "t_start": [-10, 10], "t_dec": [-30, 30], "g_min": [-0.25, 0.25], "rs_min": [-0.2, 0.2], "atr_k": [-0.5, 0.5],
             "stop_pct": [-0.1, 0.1], "t1_r": [-0.5, 0.5], "ts_min": [-30, 30], "need_ret_open": [-0.2, 0.2], "near_hod": [-0.1, 0.1], "oversold": [-0.5, 0.5]}
    for k, deltas in steps.items():
        if p.get(k) is None or isinstance(p.get(k), bool):
            continue
        for d in deltas:
            v = p[k] + d
            if v > 0 or k in ("need_ret_open",):
                out.append((f"{k} {p[k]} -> {round(v, 3)}", {**f, "params": {**p, k: v}}))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--orders", type=int, default=30)
    a = ap.parse_args()
    raw = FORMULAS.read_bytes()
    formulas = json.loads(raw)["formulas"]
    print(f"formula file sha256 {hashlib.sha256(raw).hexdigest()[:16]}…   {len(formulas)} formulas\n"
          + ("DRY RUN: the locked period is NOT used; nothing below is out-of-sample evidence" if a.dry_run else "LOCKED FINAL PERIOD " + f"{FINAL[0]} -> {FINAL[1]}"))
    fin = DESIGN if a.dry_run else FINAL
    data = load_bars(ALL11, DESIGN[0], DESIGN[1] if a.dry_run else FINAL[1])
    days = build_days(data, ALL11)
    summary = []
    for f in formulas:
        print(f"\n=== {f['id']}: {f['family']} {json.dumps(f['params'])} filters={f.get('filters', [])}")
        view(days, MAIN9, f, DESIGN, "design, 9 main symbols (tuned here)")
        view(days, HOLDOUT_SYMS, f, DESIGN, "design dates, hold-out symbols")
        recs, st = view(days, ALL11, f, fin, "FINAL period, all 11 symbols")
        view(days, MAIN9, f, fin, "FINAL period, 9 main symbols")
        view(days, HOLDOUT_SYMS, f, fin, "FINAL period, hold-out symbols")
        if f.get("reference"):
            summary.append((f["id"], st, None))
            continue
        rows = engine_view(data, days, ALL11, f, fin, a.orders)
        print("   neighbourhood on the FINAL period (one grid step away):")
        for name, g in neighbours(f)[:12]:
            r = [x for x in run_events(days, formula_events(days, ALL11, g)) if fin[0] <= x["day"] <= fin[1]]
            s = cluster_stats(r)
            print(f"      {name:<28} n={s['n']:3d} win={s['win']:5.1f}% ret/trade={s['mean']:+.3f}%")
        ok = st["n"] >= 20 and st["lo"] > 0 and st["win"] >= 45 and st["pf"] >= 1.3
        summary.append((f["id"], st, ok))
    print("\n" + "=" * 100 + "\nSUMMARY (pre-registered bar on the FINAL period: n >= 20, 95% CI of return/trade above 0, win rate >= 45%, PF >= 1.3)\n" + "=" * 100)
    for fid, st, ok in summary:
        label = "reference idea (not judged)" if ok is None else ("MEETS THE BAR" if ok else "does not meet the bar")
        print(f"  {fid:<16} {label:<28} {fmt(st)}")
    if a.dry_run:
        print("\n(dry run on design data: verdicts are not evidence)")


if __name__ == "__main__":
    main()
