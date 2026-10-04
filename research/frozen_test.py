"""The pre-registered out-of-sample test (research/PREREGISTRATION.md). Run it once on the held-out period:

    python -m research.frozen_test --start 2026-01-02 --end 2026-08-02
    python -m research.frozen_test --start 2026-08-03 --end 2026-10-02 --dry-run     # code check on NOT-out-of-sample data

Nothing in here is tuned: the candidates, parameters and decision rules are the ones written down before the data was looked at."""
import argparse
import hashlib

from bot.intraday.strategies import Range, Scalp, Trend

from .alpha import ReversionX, XD, alpha_recs, ag, build_daybars, e2, e2x, e3, e4, e5, gen_generic
from .common import *                                                      # noqa: F401,F403
from .ridge import corr, rank
from .scalp_lab import XP, agg, boot, gen_events, recs_of, sim, spy_table

SYMBOLS = "KO,MU,IBM,SPY,AAPL,MSFT,NVDA,QQQI,TSLA,META,ABBV"
P_ALL = XP(window_end_min=330, reg_close_above_vwap=False, reg_vwap_rising=False, reg_ema_order=False, reg_emas_rising=False,
           reg_crosses=False, reg_or_break=False, max_pullbacks_per_day=999, max_stop_pct=0.0, block_levels=False, capture=True)
fee_ok = lambda e: 0.02 <= 0.10 * (e.sig.entry - e.sig.stop)
SPEC_EXITS = dict(t1_r=1.5, t2_r=2.5, t1_frac=0.5, ts_min=25, be_after_t1=True)
PRESET_EXITS = dict(t1_r=1.0, t2_r=None, t1_frac=1.0, ts_min=15, be_after_t1=True)
RESULTS: list[tuple[str, str, str]] = []          # (id, what was measured, verdict)


def head(t):
    print("\n" + "=" * 100 + "\n" + t + "\n" + "=" * 100)


def pf_of(rs):
    pos, neg = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    return pos / neg if neg else 9.99


def engine_candidate(pid, name, factory, symbols, data, orders=30):
    """Mean over `orders` random symbol orderings; the CI comes from the first ordering. Applies the pre-registered bar."""
    rng = random.Random(1)
    runs = []
    for _ in range(orders):
        order = symbols[:]
        rng.shuffle(order)
        runs.append(engine(factory, order, data))
    mean = lambda f: statistics.fmean(f(r) for r in runs)
    rs0 = [{"day": t["day"], "r": t["r"]} for t in runs[0].trades]
    lo, hi = boot(rs0, "r") if len(rs0) >= 8 else (float("nan"), float("nan"))
    n = mean(lambda r: len(r.trades))
    avg = mean(lambda r: statistics.fmean([t["r"] for t in r.trades] or [0.0]))
    pf = mean(lambda r: pf_of([t["r"] for t in r.trades]))
    win = mean(lambda r: sum(t["r"] > 0 for t in r.trades) / max(1, len(r.trades)) * 100)
    ret = mean(lambda r: r.stats["return_pct"])
    h1 = mean(lambda r: statistics.fmean([t["r"] for t in design(r.trades)] or [0.0]))
    h2 = mean(lambda r: statistics.fmean([t["r"] for t in holdout(r.trades)] or [0.0]))
    print(f"{pid} {name}: trades {n:.0f} | avg R {avg:+.3f} (CI of 1st ordering [{lo:+.2f},{hi:+.2f}]) | PF {pf:.2f} | win {win:.1f}% | "
          f"return {ret:+.2f}% (orderings {min(r.stats['return_pct'] for r in runs):+.2f}..{max(r.stats['return_pct'] for r in runs):+.2f}) | "
          f"avg R by half: {h1:+.3f} / {h2:+.3f}")
    checks = {"n>=30": n >= 30, "CI above 0": lo > 0, "PF>=1.3": pf >= 1.3, "both halves positive": h1 > 0 and h2 > 0}
    print("    bar: " + "  ".join(f"{k} {'PASS' if v else 'fail'}" for k, v in checks.items()))
    verdict = "GOOD (meets the pre-registered bar)" if all(checks.values()) else ("positive but below the bar" if avg > 0 else "not profitable")
    RESULTS.append((pid, f"{name}: avg R {avg:+.3f}, PF {pf:.2f}, {n:.0f} trades, return {ret:+.2f}%", verdict))


def alpha_candidate(pid, name, recs, direction):
    """direction -1: predicted alpha < 0; +1: predicted alpha > 0."""
    if len(recs) < 8:
        RESULTS.append((pid, f"{name}: only {len(recs)} events", "too few events")); print(f"{pid} {name}: only {len(recs)} events"); return
    lo, hi = boot(recs, "a_r")
    m = statistics.fmean(r["a_r"] for r in recs)
    print(ag(recs, f"{pid} {name}"))
    if direction < 0:
        verdict = "CONFIRMED (alpha < 0)" if hi < 0 else ("direction right, not significant" if m < 0 else "CONTRADICTED (alpha > 0)")
    else:
        verdict = "CONFIRMED (alpha > 0)" if lo > 0 else ("direction right, not significant" if m > 0 else "CONTRADICTED (alpha < 0)")
    RESULTS.append((pid, f"{name}: alpha {m:+.3f}R [{lo:+.2f},{hi:+.2f}], n={len(recs)}", verdict))


def paired_ci(a, b, keys, reps=2000, seed=5):
    by = defaultdict(lambda: [0.0, 0])
    for x, y, k in zip(a, b, keys):
        if x is None or y is None:
            continue
        by[k[0]][0] += x - y
        by[k[0]][1] += 1
    days = list(by.values())
    n = sum(c for _, c in days)
    m = sum(s for s, _ in days) / n
    rng = random.Random(seed)
    ms = []
    for _ in range(reps):
        s = c = 0
        for _ in days:
            d = days[rng.randrange(len(days))]
            s += d[0]
            c += d[1]
        ms.append(s / c)
    ms.sort()
    return m, ms[int(0.025 * reps)], ms[int(0.975 * reps)], n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--orders", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true", help="code check on data that is not out of sample; verdicts are not evidence")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    symbols = [s.strip().upper() for s in a.symbols.split(",")]
    h = hashlib.sha256((REPO / "research" / "PREREGISTRATION.md").read_bytes()).hexdigest()
    head(("DRY RUN (not out of sample) " if a.dry_run else "PRE-REGISTERED OUT-OF-SAMPLE TEST ") + f"{start} -> {end}")
    print(f"pre-registration file sha256 {h[:16]}…   symbols {symbols}")
    data = load_bars(symbols, start, end)
    symbols = [s for s in symbols if data[s]]
    half = set_split(data, 0.5)
    print(f"days per symbol: { {t: len(days_of(data[t])) for t in symbols} }\nfirst half ends {half}; second half after that")
    spy = spy_table(data)
    DB = build_daybars(data, symbols)

    head("P1/P2  The spec strategies in the real engine (mean of random symbol orderings)")
    engine_candidate("P1", "Scalp A (spec)", lambda: Scalp(), symbols, data, a.orders)
    engine_candidate("P2b", "Trend B (spec)", lambda: Trend(), symbols, data, a.orders)
    engine_candidate("P2c", "Range C (spec)", lambda: Range(), symbols, data, a.orders)

    head("P3  Scalp pullback trigger vs random entries (alpha), spec window, fee-eligible")
    ev_all = gen_events(P_ALL, symbols, data, spy)
    pool = [e for e in ev_all if e.sig.elapsed <= 120 and fee_ok(e)]
    alpha_candidate("P3", "A pullback trigger", alpha_recs(pool, DB), -1)

    head("P4  Exit rules on the same trigger pool: '1R all-out + 15 min time stop' vs the spec exits (paired)")
    keys = [(e.day, e.ticker) for e in pool]
    base = [(lambda s: None if s is None else s["ret_pct"])(sim(e, flat_min=e.sig.flat_min, **SPEC_EXITS)) for e in pool]
    alt = [(lambda s: None if s is None else s["ret_pct"])(sim(e, flat_min=e.sig.flat_min, **PRESET_EXITS)) for e in pool]
    m, lo, hi, n = paired_ci(alt, base, keys)
    wr = lambda kw: statistics.fmean([(sim(e, flat_min=e.sig.flat_min, **kw)["r"] > 0) * 100 for e in pool if sim(e, flat_min=e.sig.flat_min, **kw)] or [0])
    print(f"   spec exits win {wr(SPEC_EXITS):.1f}%  | preset win {wr(PRESET_EXITS):.1f}%  | paired gain {m:+.4f}% per trade [{lo:+.4f},{hi:+.4f}] (n={n})")
    RESULTS.append(("P4", f"preset exits vs spec exits: paired gain {m:+.4f}% per trade [{lo:+.4f},{hi:+.4f}]",
                    "CONFIRMED (gain > 0)" if lo > 0 else ("direction right, not significant" if m > 0 else "CONTRADICTED (gain < 0)")))

    head("P5  Breakout-style entries vs random entries (alpha)")
    for pid, name, fn, kw in (("P5a", "opening-range breakout", e3, dict(ts_min=None, t2_r=None)), ("P5b", "high-of-day break", e4, dict(ts_min=60))):
        ev = [e for e in gen_generic(fn, symbols, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
        alpha_candidate(pid, name, alpha_recs(ev, DB, **kw), -1)

    head("P6  VWAP reversion long (k=1.5 ATR below VWAP + bounce bar): absolute result and alpha")
    ev = [e for e in gen_generic(e2(1.5), symbols, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
    absr = recs_of(ev, hod_t2=False)
    print(agg(absr, "   absolute result (no null)"))
    alo, ahi = boot(absr, "r")
    ar = alpha_recs(ev, DB)
    alpha_candidate("P6", "reversion alpha", ar, +1)
    mabs = statistics.fmean(r["r"] for r in absr)
    RESULTS.append(("P6", f"reversion absolute R {mabs:+.3f} [{alo:+.2f},{ahi:+.2f}], n={len(absr)}",
                    "CONFIRMED (R > 0)" if alo > 0 else ("direction right, not significant" if mabs > 0 else "CONTRADICTED (R < 0)")))

    head("P7/P8  VWAP reversion in the real engine")
    engine_candidate("P7", "ReversionX k=1.5", lambda: ReversionX(), symbols, data, a.orders)
    engine_candidate("P8", "ReversionX k=2.0", lambda: ReversionX(XD(k_atr=2.0)), symbols, data, a.orders)

    head("P9  Does depth below VWAP predict the reversion trade's R?  (Spearman, day-clustered CI)")
    ex = [e for e in gen_generic(e2x(1.0), symbols, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
    R9 = recs_of(ex, hod_t2=False)
    x = [r["f"]["depth"] for r in R9]
    y = [r["r"] for r in R9]
    rho = corr(rank(x), rank(y))
    by = defaultdict(list)
    for r in R9:
        by[r["day"]].append(r)
    days, rng, vals = list(by), random.Random(3), []
    for _ in range(2000):
        smp = []
        for _ in days:
            smp += by[days[rng.randrange(len(days))]]
        vals.append(corr(rank([s["f"]["depth"] for s in smp]), rank([s["r"] for s in smp])))
    vals.sort()
    lo, hi = vals[50], vals[1949]
    print(f"   n={len(R9)}  Spearman rho = {rho:+.3f}  95% CI [{lo:+.3f},{hi:+.3f}]")
    RESULTS.append(("P9", f"depth vs R: rho {rho:+.3f} [{lo:+.3f},{hi:+.3f}], n={len(R9)}",
                    "CONFIRMED (rho > 0)" if lo > 0 else ("direction right, not significant" if rho > 0 else "CONTRADICTED (rho < 0)")))

    head("P10  Other oversold definitions (same bounce bar, stop and exits): alpha, and the absolute result for context")
    for pid, name, fn in (("P10a", "RSI(14, 5m) < 30", e5("rsi", 30)), ("P10b", "5m close below the BB(20, 1.5) lower band", e5("bb", 1.5))):
        ev = [e for e in gen_generic(fn, symbols, data, spy) if fee_ok(e) and e.feats["stop_pct"] <= 1.5]
        print(agg(recs_of(ev, hod_t2=False), f"   {name}, absolute (no null)"))
        alpha_candidate(pid, name, alpha_recs(ev, DB), +1)

    head("P11  Trend (B) entry timing vs random 5-minute entries (alpha)")
    from .trend_lab import XB, b_alpha_recs, build_dayx, gen_events_b
    pall = XB(window_end_min=330, reg_close_above_vwap=False, reg_above_or=False, reg_ema_order=False, reg_vwap_rising=False,
              reg_crosses=False, min_stop_pct=0.0, max_stop_pct=0.0, one_per_day=False, capture=True)
    eb = [e for e in gen_events_b(pall, symbols, data, spy) if 30 <= e.sig.elapsed <= 150 and 0.3 <= e.feats["stop_pct"] <= 1.0 and fee_ok(e)]
    alpha_candidate("P11", "B pullback trigger (regime off)", b_alpha_recs(eb, build_dayx(data, symbols)), -1)

    head("SUMMARY against the pre-registered predictions")
    for pid, what, verdict in RESULTS:
        print(f"  {pid:<4} {verdict:<36} {what}")
    if a.dry_run:
        print("\n(dry run: this window is the design data, so none of the verdicts above are out-of-sample evidence)")


if __name__ == "__main__":
    main()
