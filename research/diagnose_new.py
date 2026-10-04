"""Design-period diagnostics for the formulas chosen by research.search_new: concentration, neighbouring parameters, the real engine with
portfolio limits, and the plain (unadjusted) version of each idea for comparison. Uses Jan 2 - Jul 31 and the nine main symbols only."""
import json
import sys

from .common import *                                                      # noqa: F401,F403
from .daylab import Replay, build_days, run_events
from .families import DEFAULTS, make_events
from .final_new import formula_events, neighbours
from .search import MAIN9, cluster_stats, fmt
from .search_new import DESIGN, apply_filter

PLAIN = {   # the idea as originally described, with the family defaults except where stated
    "GF": [("buy gap-downs >=0.5%, enter 09:45, LOD stop, half at 50% fill, rest at prior close", dict(g_min=0.5, g_max=99.0, t_start=15, t_end=15)),
           ("same, gap-downs >=1%", dict(g_min=1.0, g_max=99.0, t_start=15, t_end=15))],
    "GG": [("buy gap-ups >=1%, break of the first 5-min high, ORL stop, 1.5R/3R", dict(g_min=1.0, trigger="orh5")),
           ("buy gap-ups >=0.5%, break of the first 15-min high", dict(g_min=0.5, trigger="orh15"))],
    "RS": [("leader: >=0.5% ahead of SPY at 10:30, above VWAP, buy now, VWAP stop", dict(t_dec=60, rs_min=0.5)),
           ("leader at 11:30, >=1% ahead", dict(t_dec=120, rs_min=1.0)),
           ("laggard bounce: >=1% behind SPY at 10:30, green bar", dict(side="laggard", t_dec=60, rs_min=1.0))],
    "LH": [("momentum: up >=0.6% on the day at 15:00, above VWAP, hold to 15:55", dict(t_ent=330, need_ret_open=0.6, need_vwap=True)),
           ("momentum: within 0.3% of the high of day at 15:00", dict(t_ent=330, near_hod=0.3)),
           ("momentum: SPY up and stock leading SPY at 15:30", dict(t_ent=360, spy_up=True, rs_min=0.3)),
           ("first half-hour up and above VWAP at 14:00", dict(t_ent=270, need_first30=0.0, need_vwap=True)),
           ("oversold bounce: >=1 ATR below VWAP at 15:30", dict(t_ent=360, oversold=1.0))],
}


def engine_rows(evs, data, orders=30):
    rng, rows = random.Random(1), []
    for _ in range(orders):
        order = list(MAIN9)
        rng.shuffle(order)
        res = engine(lambda: Replay(evs), order, {t: data[t] for t in order})
        rs = [t["r"] for t in res.trades]
        pos, neg = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
        rows.append((len(rs), sum(x > 0 for x in rs) / len(rs) * 100 if rs else 0, statistics.fmean(rs) if rs else 0, pos / neg if neg else 9.99, res.stats["return_pct"]))
    m = lambda k: statistics.fmean(r[k] for r in rows)
    return f"trades {m(0):.0f} | win {m(1):.1f}% | avg R {m(2):+.3f} | PF {m(3):.2f} | return {m(4):+.2f}% (orderings {min(r[4] for r in rows):+.2f}..{max(r[4] for r in rows):+.2f})"


def main():
    data = load_bars(MAIN9, DESIGN[0], DESIGN[1])
    days = build_days(data, MAIN9)
    for fam in ("GF", "GG", "RS", "LH"):
        j = json.load(open(REPO / "data" / "research" / f"search_{fam}.json"))["best"]
        print("\n" + "=" * 110 + f"\n{fam}\n" + "=" * 110)
        print("the plain idea (no tuning):")
        for name, kw in PLAIN[fam]:
            recs = run_events(days, make_events(days, MAIN9, fam, **kw))
            print(f"   {name:<84}\n      {fmt(cluster_stats(recs))}")
        forms = [("E", {"family": fam, "params": j["params"], "filters": j["filters"]})]
        if "win_formula" in j and (j["win_formula"]["params"] != j["params"] or j["win_formula"]["filters"] != j["filters"]):
            forms.append(("W", {"family": fam, "params": j["win_formula"]["params"], "filters": j["win_formula"]["filters"]}))
        core = {"family": fam, "params": j["params"], "filters": []}
        forms.append(("core (no extra signals)", core))
        for label, f in forms:
            evs = formula_events(days, MAIN9, f)
            recs = run_events(days, evs)
            st = cluster_stats(recs)
            print(f"\n[{label}] filters={f['filters']}\n   {fmt(st)}")
            tot = sum(r["acct_ret"] for r in recs)
            top5 = sum(sorted((r["acct_ret"] for r in recs), reverse=True)[:5])
            print(f"   the 5 best trades supply {top5 / tot * 100 if tot else 0:.0f}% of the total return; without them {(tot - top5) / max(1, len(recs) - 5):+.3f}% per trade")
            bysym = defaultdict(list)
            for r in recs:
                bysym[r["t"]].append(r["acct_ret"])
            print("   by symbol (n, mean %): " + "  ".join(f"{t}:{len(v)},{statistics.fmean(v):+.2f}" for t, v in sorted(bysym.items())))
            loo = [(statistics.fmean([r["acct_ret"] for r in recs if r["t"] != t]) if any(r["t"] != t for r in recs) else 0.0) for t in bysym]
            print(f"   leave-one-symbol-out mean return per trade: min {min(loo):+.3f}% max {max(loo):+.3f}%")
            nb = []
            for name, g in neighbours(f):
                r = run_events(days, formula_events(days, MAIN9, g))
                s = cluster_stats(r)
                nb.append((name, s))
            if nb:
                pos = sum(1 for _, s in nb if s["mean"] > 0)
                print(f"   neighbouring parameters: {pos}/{len(nb)} have a positive mean; " + "; ".join(f"{n}: {s['mean']:+.3f}% (n={s['n']})" for n, s in nb[:6]))
            print("   engine (random orderings): " + engine_rows(evs, data))


if __name__ == "__main__":
    main()
