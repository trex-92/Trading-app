"""Reproduces the October 2026 strategy search on whatever 1-minute history is cached in data/cache.

    python -m research.run_all                      # from the repo root, with the venv active
    python -m research.run_all --holdout-share 0.3 --skip-slow

Nothing here places orders or calls the broker: it only reads data/cache and runs the repo's own backtester.
Read research/README.md (and docs/strategy-search-2026-10.md) before drawing conclusions from the output."""
import argparse
import itertools

from .alpha import (ReversionX, XD, _Ev, alpha_recs, ag, build_daybars, e1, e2, e3, e4, gen_generic)
from .common import *                                                      # noqa: F401,F403
from .reality_check import paired_stats, reality_check
from .ridge import Model, corr, rank
from .scalp_lab import ScalpX, XP, agg, gen_events, recs_of, sim, spy_table
from bot.intraday.strategies import Range, Scalp, Trend

# every Scalp trigger, no regime / stop / key-level filters, window widened to 14:50
P_ALL = XP(window_end_min=330, reg_close_above_vwap=False, reg_vwap_rising=False, reg_ema_order=False, reg_emas_rising=False,
           reg_crosses=False, reg_or_break=False, max_pullbacks_per_day=999, max_stop_pct=0.0, block_levels=False, capture=True)
fee_ok = lambda e: 0.02 <= 0.10 * (e.sig.entry - e.sig.stop)               # the engine's cost-vs-1R rule at $0.02/share


def head(text):
    print("\n" + "=" * 100 + "\n" + text + "\n" + "=" * 100)


def section_context(data, main):
    head("1. Market context: where did the basket's return come from?  (sum of daily %, equal weight)")
    first = sorted({d for t in main for d in days_of(data[t])})[0]
    out = {"D": [0.0, 0.0], "H": [0.0, 0.0]}
    for t in main:
        byday = {}
        for b in data[t]:
            if US.is_regular(b.ts):
                byday.setdefault(b.ts.astimezone(US.tz).date(), []).append(b)
        ds = sorted(byday)
        for i, d in enumerate(ds):
            if d <= first:
                continue
            k = "D" if d <= SPLIT["design_end"] else "H"
            out[k][0] += (byday[d][-1].close / byday[d][0].open - 1) * 100 / len(main)
            out[k][1] += (byday[d][0].open / byday[ds[i - 1]][-1].close - 1) * 100 / len(main)
    print(f"  design  : intraday {out['D'][0]:+.2f}%   overnight {out['D'][1]:+.2f}%")
    print(f"  holdout : intraday {out['H'][0]:+.2f}%   overnight {out['H'][1]:+.2f}%")
    print("  (an intraday long-only strategy can only earn the intraday part; a strong intraday tape flatters any long strategy)")


def section_baseline(data, main):
    head("2. The three strategies exactly as specified, in the real engine")
    for name, cls in (("A Scalp", Scalp), ("B Trend", Trend), ("C Range", Range)):
        r = engine(lambda: cls(), main, data)
        print(agg(r.trades, name + " all", ci=False)); print(agg(design(r.trades), "   design", ci=False)); print(agg(holdout(r.trades), "   holdout", ci=False))


def section_pool(R):
    head("3. Every Scalp pullback trigger (no filters), spec window 09:45-11:30, fee rule applied")
    W = [r for r in R if r["f"]["elapsed"] <= 120 and fee_ok(r["ev"])]
    print(agg(W, "all triggers")); print(agg(design(W), "   design")); print(agg(holdout(W), "   holdout"))
    conds = {"close>VWAP (5m)": lambda f: f["fl_close_above_vwap"], "VWAP rising": lambda f: f["fl_vwap_rising"], "EMA9>EMA20": lambda f: f["fl_ema_order"],
             "EMAs both rising": lambda f: f["fl_emas_rising"], "VWAP crosses<=2": lambda f: f["fl_crosses"] <= 2, "5m close>OR high seen": lambda f: f["fl_or_break"],
             "stop<=0.25%": lambda f: f["stop_pct"] <= 0.25, "no key level in 1.5R": lambda f: f["n_block"] == 0, "pullback #1-2": lambda f: f["pb_n"] <= 2}
    print("\n  each spec condition alone, pass vs fail (raw R, not adjusted for the tape):")
    for name, fn in conds.items():
        print("  " + agg([r for r in W if fn(r["f"])], "PASS " + name, ci=False)); print("  " + agg([r for r in W if not fn(r["f"])], "FAIL " + name, ci=False))
    for r in W:
        r["score"] = sum(fn(r["f"]) for fn in conds.values())
    print("\n  expectancy by number of spec conditions passed (does it rise with more filters?):")
    for s in range(3, 10):
        g = [r for r in W if r["score"] == s]
        if g:
            print("  " + agg(g, f"score={s}", ci=False))


def section_alpha(data, main, spy, DB, ev_all):
    head("4. Entry-timing alpha: actual entry minus random entries on the same symbol-day (same time of day, same stop %, same exits)")
    pool = [e for e in ev_all if e.sig.elapsed <= 120 and fee_ok(e)]
    A = alpha_recs(pool, DB)
    print(ag(A, "A pullback trigger (all)")); print(ag([r for r in A if r["day"] <= SPLIT["design_end"]], "   design"))
    print(ag([r for r in A if r["day"] > SPLIT["design_end"]], "   holdout"))
    spec = [e for e in gen_events(XP(capture=True), main, data, spy) if fee_ok(e)]
    print(ag(alpha_recs(spec, DB), "the spec's own signals"))
    for name, fn in (("EMA9>EMA20", lambda f: f["fl_ema_order"]), ("5m close>OR high seen", lambda f: f["fl_or_break"]), ("no key level in 1.5R", lambda f: f["n_block"] == 0)):
        print(ag([r for r in A if fn(r["f"])], "PASS " + name)); print(ag([r for r in A if not fn(r["f"])], "FAIL " + name))
    print("\n  alternative entry families (one canonical setting each, not tuned):")
    fams = {"E1a buy the dip, spec regime": (e1(True), {}), "E1b buy the dip, no regime": (e1(False), {}), "E2a VWAP reversion k=1.5": (e2(1.5), {}),
            "E2b VWAP reversion k=1.0": (e2(1.0), {}), "E3 opening-range breakout": (e3, dict(ts_min=None, t2_r=None)), "E4 high-of-day break": (e4, dict(ts_min=60))}
    for name, (fn, kw) in fams.items():
        ev = [e for e in gen_generic(fn, main, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
        r = alpha_recs(ev, DB, **kw)
        print(ag(r, name)); print(ag([x for x in r if x["day"] <= SPLIT["design_end"]], "   design")); print(ag([x for x in r if x["day"] > SPLIT["design_end"]], "   holdout"))
        if name.startswith("E2a"):
            print(agg(recs_of(ev, hod_t2=False), "   absolute result, no null"))


def section_ridge(R):
    head("5. Do the captured features predict a trigger's R out of sample?  (ridge regression, fit on design, scored on holdout)")
    D, H = design(R), holdout(R)
    days = sorted({r["day"] for r in D})
    blocks = [set(days[i * len(days) // 5:(i + 1) * len(days) // 5]) for i in range(5)]
    print(f"  design n={len(D)}  holdout n={len(H)}")
    print("  lambda   CV-corr(blocked)   HOLDOUT corr   spearman   holdout avgR: top-fifth | bottom-fifth of predictions")
    for lam in (30, 300, 3000, 30000):
        cvp, cvy = [], []
        for fb in blocks:
            tr = [r for r in D if r["day"] not in fb]; te = [r for r in D if r["day"] in fb]
            cvp += Model().fit(tr, [r["r"] for r in tr], lam).predict(te); cvy += [r["r"] for r in te]
        p = Model().fit(D, [r["r"] for r in D], lam).predict(H); y = [r["r"] for r in H]
        o = sorted(range(len(p)), key=lambda i: p[i]); q = len(p) // 5
        print(f"  {lam:6d}   {corr(cvp, cvy):+.3f}             {corr(p, y):+.3f}         {corr(rank(p), rank(y)):+.3f}     "
              f"{statistics.fmean(y[i] for i in o[-q:]):+.3f} | {statistics.fmean(y[i] for i in o[:q]):+.3f}")


def section_exits(ev_all):
    head("6. Scalp exit rules: 170 pre-defined variants, paired against the spec exits, with White's reality check")
    EV = [e for e in ev_all if e.sig.elapsed <= 120 and fee_ok(e)]
    keys = [(e.day, e.ticker) for e in EV]
    grid = []
    for t1, t2, frac, ts, be in itertools.product((0.75, 1.0, 1.5, 2.0), (None, 2.0, 2.5, 3.5), (0.5, 1.0), (15, 25, 45, 90, None), (True, False)):
        if (t2 is not None and t2 <= t1) or (frac == 1.0 and (t2 is not None or not be)):
            continue
        grid.append(dict(t1_r=t1, t2_r=t2, t1_frac=frac, ts_min=ts, be_after_t1=be))
    run_v = lambda kw: [(lambda s: None if s is None else (s["ret_pct"], s["r"]))(sim(e, flat_min=e.sig.flat_min, **kw)) for e in EV]
    base_kw = dict(t1_r=1.5, t2_r=2.5, t1_frac=0.5, ts_min=25, be_after_t1=True)
    base = run_v(base_kw)

    def st(vals, per):
        sel = [v for v, k in zip(vals, keys) if v is not None and ((k[0] <= SPLIT["design_end"]) == (per == "D"))]
        rs = [v[1] for v in sel]
        pos, neg = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
        return len(sel), statistics.fmean(v[0] for v in sel), sum(r > 0 for r in rs) / len(rs) * 100, statistics.fmean(rs), (pos / neg if neg else 9.99)
    bd, bh = st(base, "D"), st(base, "H")
    print(f"  spec exits: design n={bd[0]} ret/trade={bd[1]:+.4f}% win={bd[2]:.1f}% avgR={bd[3]:+.3f} PF={bd[4]:.2f} | holdout n={bh[0]} ret/trade={bh[1]:+.4f}% win={bh[2]:.1f}% avgR={bh[3]:+.3f} PF={bh[4]:.2f}")
    res = [(kw, run_v(kw)) for kw in grid]
    rows = sorted([(st(v, "D"), st(v, "H"), kw, v) for kw, v in res], key=lambda r: -r[0][1])
    print("  top 6 by DESIGN return per trade  [design: ret win avgR PF | holdout: ret win avgR PF]")
    for d, h, kw, _ in rows[:6]:
        print(f"   t1={kw['t1_r']} t2={kw['t2_r']} frac={kw['t1_frac']} ts={kw['ts_min']} be={kw['be_after_t1']!s:<5} | {d[1]:+.4f} {d[2]:4.1f} {d[3]:+.3f} {d[4]:4.2f} | {h[1]:+.4f} {h[2]:4.1f} {h[3]:+.3f} {h[4]:4.2f}")
    print(f"  better than the spec exits: design {sum(r[0][1] > bd[1] for r in rows)}/{len(rows)}, holdout {sum(r[1][1] > bh[1] for r in rows)}/{len(rows)}, both {sum(r[0][1] > bd[1] and r[1][1] > bh[1] for r in rows)}")
    print("  rank correlation of variant performance, design vs holdout:", round(corr(rank([r[0][1] for r in rows]), rank([r[1][1] for r in rows])), 3))
    ret = lambda vals: [None if v is None else v[0] for v in vals]
    tmax, p = reality_check([ret(r[3]) for r in rows], ret(base), keys, "D", reps=1000)
    print(f"  White reality check on the design period ({len(rows)} variants): best paired t = {tmax:.2f}, adjusted p = {p:.3f}")
    for name, kw in (("1R all-out, 15m time stop", dict(t1_r=1.0, t2_r=None, t1_frac=1.0, ts_min=15, be_after_t1=True)),
                     ("spec but 15m time stop", dict(t1_r=1.5, t2_r=2.5, t1_frac=0.5, ts_min=15, be_after_t1=True))):
        v = ret(run_v(kw))
        for per in ("D", "H"):
            m, se, n = paired_stats(v, ret(base), keys, per)
            print(f"   {name:<28} {'design ' if per == 'D' else 'holdout'}: paired gain {m:+.4f}% per trade (z={m / se:+.2f}, n={n})")


def section_reversion(data, main):
    head("7. VWAP-reversion long (buy 1.5 ATR below VWAP after a bounce bar) in the real engine, vs the spec Scalp")
    r = engine(lambda: ReversionX(), main, data)
    print(agg(r.trades, "D reversion")); print(agg(design(r.trades), "   design")); print(agg(holdout(r.trades), "   holdout"))
    print(f"   return {r.stats['return_pct']:+.2f}% | max drawdown {r.stats['max_drawdown_pct']}% | trades/day {len(r.trades) / max(1, len({t['date'] for t in r.trades})):.2f}")


def section_unseen(data, unseen, spy):
    head(f"8. Symbols never used in any analysis above: {', '.join(unseen)}")
    DBu = build_daybars(data, unseen)
    pool = [e for e in gen_events(P_ALL, unseen, data, spy) if e.sig.elapsed <= 120 and fee_ok(e)]
    print(ag(alpha_recs(pool, DBu), "A pullback trigger (alpha)"))
    for name, fn, kw in (("E2a VWAP reversion", e2(1.5), {}), ("E3 opening-range breakout", e3, dict(ts_min=None, t2_r=None)), ("E4 high-of-day break", e4, dict(ts_min=60))):
        ev = [e for e in gen_generic(fn, unseen, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
        print(ag(alpha_recs(ev, DBu, **kw), name))
        if name.startswith("E2a"):
            print(agg(recs_of(ev, hod_t2=False), "   absolute result, no null"))
    for name, cls in (("spec Scalp A", lambda: ScalpX(XP())), ("reversion D", lambda: ReversionX())):
        r = engine(cls, unseen, data)
        print(agg(r.trades, f"engine: {name}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma list; default: every symbol with >=80%% of the longest cached history")
    ap.add_argument("--unseen", help="comma list kept out of everything; default: symbols with shorter cached history")
    ap.add_argument("--holdout-share", type=float, default=0.3)
    ap.add_argument("--start", help="first cached day to use (YYYY-MM-DD); default: everything cached")
    ap.add_argument("--end", help="last cached day to use")
    ap.add_argument("--design-end", help="last day of the design period; default: split by --holdout-share")
    ap.add_argument("--skip-slow", action="store_true", help="skip the exit grid and the ridge check")
    a = ap.parse_args()
    start = date.fromisoformat(a.start) if a.start else None
    end_ = date.fromisoformat(a.end) if a.end else None
    auto_main, auto_unseen = pick_universe(start=start, end=end_)
    main_syms = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else auto_main
    unseen = [t.strip().upper() for t in a.unseen.split(",")] if a.unseen else auto_unseen
    data = load_bars(main_syms + unseen, start, end_)
    end = set_split(data, a.holdout_share, date.fromisoformat(a.design_end) if a.design_end else None)
    ndays = {t: len(days_of(data[t])) for t in data}
    print(f"symbols: {main_syms}\nunseen : {unseen}\ndays per symbol: {ndays}\ndesign period ends {end}; holdout is after that date")
    print(f"costs assumed: ${base_shared(US).cost_per_share_round_trip}/share round trip (a placeholder; see README)")
    spy = spy_table(data, "SPY") if "SPY" in data else {}
    DB = build_daybars(data, main_syms)
    section_context(data, main_syms)
    section_baseline(data, main_syms)
    ev_all = gen_events(P_ALL, main_syms, data, spy)
    R = recs_of(ev_all)
    section_pool(R)
    section_alpha(data, main_syms, spy, DB, ev_all)
    if not a.skip_slow:
        section_ridge(R)
        section_exits(ev_all)
    section_reversion(data, main_syms)
    if unseen:
        section_unseen(data, unseen, spy)


if __name__ == "__main__":
    main()
