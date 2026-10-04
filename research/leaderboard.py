"""Leaderboard: every engine-executable strategy tested in this work, run over ALL downloaded data (2026-01-02 -> 2026-10-02,
11 symbols) in the repo's real Backtester (one position at a time, at most 3 trades a day, shared risk limits), averaged over random
symbol orderings, with Moomoo's US schedule: 0.03% commission per order plus a $0.99 platform fee per order (each partial exit is its own
order). Profit % = total P&L / starting budget (all-in at 1x notional unless the stop is wider than 1%).

Run at three account sizes (the fixed fee per order matters far more on small trades), with the spec's rule "skip the trade when fees exceed
10% of the risk" both on and off. Fractional shares are assumed to be available.

    python -m research.leaderboard            # writes data/research/leaderboard.json and prints the table

Most strategies here were tuned on part of this data (the tuning period is flagged), so the whole-period profit is optimistic; the locked
final period (2026-08-03 -> 2026-10-02) was not used to tune the new formulas and is the honest column for them."""
import json
import sys
from concurrent.futures import ProcessPoolExecutor

from bot.intraday.strategies import Range, Scalp, Trend

from .alpha import ReversionX, XD
from .common import *                                                      # noqa: F401,F403
from .daylab import Replay, build_days
from .final_new import ALL11, formula_events
from .search_new import DESIGN
from .search import FINAL

BUDGETS = (1000.0, 10000.0, 100000.0)
RULES = ("on", "off")
ORDERS = 30
OUT = REPO / "data" / "research" / "leaderboard.json"

# id, display name, one-line description, evidence label (what the data says about it, from the pre-registered tests)
SPECS = [
    ("A", "A · Scalp (original spec)", "VWAP pullback scalp, the strategy as specified", "original",
     "Original spec, not tuned on this data. Pre-registered Jan–Jul test: not profitable"),
    ("B", "B · Trend (original spec)", "5-minute trend pullback, the strategy as specified", "original",
     "Original spec, not tuned on this data. Pre-registered Jan–Jul test: positive, not significant"),
    ("C", "C · Range (original spec)", "initial-balance range bounce, the strategy as specified", "original",
     "Original spec, not tuned on this data. Pre-registered Jan–Jul test: loses"),
    ("D15", "D · VWAP reversion, 1.5 ATR", "buy 1.5 ATR(5m) below VWAP after a bounce bar", "reversion",
     "Pre-registered Jan–Jul test: loses (−0.07R)"),
    ("D20", "D · VWAP reversion, 2.0 ATR", "buy 2.0 ATR(5m) below VWAP after a bounce bar", "reversion",
     "Setting picked on Aug–Oct. Pre-registered Jan–Jul test: about zero"),
]
FORMULA_NOTES = {
    "GF-core": ("Gap fill · profit version", "buy a 1–4% gap-down at 10:30–11:45, stop under the day's low, sell all at yesterday's close", "new",
                "Tuned on Jan–Jul. Locked Aug–Oct test: borderline (interval touches 0)"),
    "GF-E": ("Gap fill · extra signals", "gap fill plus: 8%+ below 20-day high, volatile stock, EMA9 below EMA20", "new",
             "Tuned on Jan–Jul. Locked test: too few trades to judge"),
    "GF-W": ("Gap fill · high-win-rate version", "buy a 1–4% gap-down at 10:30–11:45, 2.5 ATR stop, half off halfway to yesterday's close", "new",
             "Tuned on Jan–Jul. Locked Aug–Oct test: MET the pre-registered bar"),
    "GG-core": ("Gap and go", "buy a gap-up on the 15-minute opening-range break, VWAP stop and VWAP-loss exit", "new",
                "Tuned on Jan–Jul. Locked Aug–Oct test: failed"),
    "GG-E": ("Gap and go · volume filter", "gap and go only when volume is above its 20-day pace", "new",
             "Tuned on Jan–Jul. Locked Aug–Oct test: failed"),
    "GG-W": ("Gap and go · below prior high", "gap and go only below yesterday's high", "new",
             "Tuned on Jan–Jul. Locked Aug–Oct test: too few trades to judge"),
    "RS-core": ("Relative strength vs SPY", "buy at 11:30 a stock 1%+ ahead of SPY and above VWAP", "new",
                "Tuned on Jan–Jul. Locked Aug–Oct test: failed, significantly negative"),
    "RS-E": ("Relative strength · weak-market filter", "relative strength only when SPY is below VWAP and the stock is volatile", "new",
             "Tuned on Jan–Jul. Locked Aug–Oct test: failed"),
    "LH-core": ("Last hour · oversold bounce", "at 15:30 buy a stock 2+ ATR below VWAP while SPY is down, out by 15:55", "new",
                "Tuned on Jan–Jul. Locked Aug–Oct test: failed"),
    "LH-E": ("Last hour · extra signals", "last-hour bounce with prior-day-range and distance-from-high filters", "new",
             "Tuned on Jan–Jul. Locked Aug–Oct test: failed"),
    "ref-GF-plain": ("Plain gap fill (untuned)", "buy gap-downs of 1%+ at 09:45", "reference", "Untuned plain idea, for comparison"),
    "ref-GG-plain": ("Plain gap and go (untuned)", "buy gap-ups of 1%+ on the 5-minute opening-range break", "reference", "Untuned plain idea, for comparison"),
    "ref-RS-plain": ("Plain relative strength (untuned)", "buy a stock 0.5%+ ahead of SPY at 10:30", "reference", "Untuned plain idea, for comparison"),
    "ref-LH-momentum": ("Plain last-hour momentum (untuned)", "at 15:00 buy a stock up 0.6%+ on the day and above VWAP", "reference", "Untuned plain idea, for comparison"),
}


def all_specs():
    forms = {f["id"]: f for f in json.loads((REPO / "research" / "new_formulas.json").read_text())["formulas"]}
    specs = [{"id": i, "name": n, "desc": d, "kind": k, "evidence": e, "type": "class"} for i, n, d, k, e in SPECS]
    for fid, (n, d, k, e) in FORMULA_NOTES.items():
        specs.append({"id": fid, "name": n, "desc": d, "kind": k, "evidence": e, "type": "formula", "formula": forms[fid]})
    return specs


def _view(factory, data, dates, idx, budget, rule):
    shared = {"allow_fractional": True, "fractional_step": 0.01}
    if rule == "off":
        shared["max_cost_pct_of_1R"] = 1e9
    rng = random.Random(1)
    runs = []
    for _ in range(ORDERS):
        order = list(ALL11)
        rng.shuffle(order)
        res = engine(factory, order, data, budget=budget, shared=shared)
        tr = res.trades
        pnl = [t["pnl"] for t in tr]
        daily = [0.0] * len(dates)
        for t in tr:
            daily[idx[t["date"]]] += t["pnl"]
        cum, c = [], 0.0
        for v in daily:
            c += v
            cum.append(c / budget * 100)
        tune = [t for t in tr if date.fromisoformat(t["date"]) <= DESIGN[1]]
        fin = [t for t in tr if date.fromisoformat(t["date"]) >= FINAL[0]]
        pos, neg = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
        runs.append({"n": len(tr), "win": sum(p > 0 for p in pnl) / len(pnl) * 100 if pnl else 0.0, "ret": sum(pnl) / budget * 100,
                     "pf": min(pos / neg, 99.0) if neg else (99.0 if pos else 0.0), "dd": res.stats.get("max_drawdown_pct", 0.0),
                     "r": statistics.fmean(t["r"] for t in tr) if tr else 0.0, "fees": sum(t["costs"] for t in tr) / budget * 100,
                     "n_tune": len(tune), "ret_tune": sum(t["pnl"] for t in tune) / budget * 100,
                     "n_final": len(fin), "ret_final": sum(t["pnl"] for t in fin) / budget * 100,
                     "win_final": sum(t["pnl"] > 0 for t in fin) / len(fin) * 100 if fin else 0.0, "cum": cum})
    m = lambda k: statistics.fmean(r[k] for r in runs)
    return {"trades": m("n"), "win": m("win"), "ret": m("ret"), "ret_min": min(r["ret"] for r in runs), "ret_max": max(r["ret"] for r in runs),
            "pf": m("pf"), "dd": m("dd"), "avg_r": m("r"), "fees": m("fees"), "trades_tune": m("n_tune"), "ret_tune": m("ret_tune"),
            "trades_final": m("n_final"), "ret_final": m("ret_final"), "ret_final_min": min(r["ret_final"] for r in runs),
            "ret_final_max": max(r["ret_final"] for r in runs), "win_final": m("win_final"),
            "cum": [round(statistics.fmean(r["cum"][i] for r in runs), 3) for i in range(len(dates))]}


def _run(spec):
    """Worker: one strategy at every account size and fee-rule setting. Returns its views and the list of dates."""
    data = load_bars(ALL11, date(2026, 1, 2), date(2026, 10, 2))
    days = build_days(data, ALL11)
    if spec["type"] == "formula":
        evs = formula_events(days, ALL11, spec["formula"])
        factory = lambda: Replay(evs)
    else:
        factory = {"A": lambda: Scalp(), "B": lambda: Trend(), "C": lambda: Range(), "D15": lambda: ReversionX(XD()),
                   "D20": lambda: ReversionX(XD(k_atr=2.0))}[spec["id"]]
    dates = sorted({d.day for d in days.values()})
    idx = {d.isoformat(): i for i, d in enumerate(dates)}
    views = {f"{int(b)}_{rule}": _view(factory, data, dates, idx, b, rule) for b in BUDGETS for rule in RULES}
    out = {k: spec[k] for k in ("id", "name", "desc", "kind", "evidence")}
    out["views"] = views
    return out, [d.isoformat() for d in dates]


def benchmarks():
    data = load_bars(ALL11, date(2026, 1, 2), date(2026, 10, 2))
    per = {}
    for t in ALL11:
        reg = [b for b in data[t] if US.is_regular(b.ts)]
        per[t] = (reg[-1].close / reg[0].open - 1) * 100
    return {"equal_weight_buy_hold_pct": statistics.fmean(per.values()), "spy_buy_hold_pct": per["SPY"], "per_symbol": per}


def main():
    specs = all_specs()
    results, dates = {}, None
    with ProcessPoolExecutor(max_workers=6) as ex:
        for spec, (res, dts) in zip(specs, ex.map(_run, specs)):
            results[spec["id"]] = res
            dates = dts
            v = res["views"]["100000_on"], res["views"]["10000_on"]
            print(f"done {spec['id']:<16} $100k rule-on: {v[0]['trades']:6.1f} trades {v[0]['ret']:+7.2f}% | $10k rule-on: {v[1]['trades']:6.1f} trades {v[1]['ret']:+7.2f}%", flush=True)
    doc = {"period": {"start": dates[0], "end": dates[-1], "tuning_end": DESIGN[1].isoformat(), "final_start": FINAL[0].isoformat()},
           "budgets": list(BUDGETS), "rules": list(RULES), "orderings": ORDERS, "symbols": ALL11, "benchmarks": benchmarks(), "dates": dates,
           "costs": {"commission_pct_each_way": 0.03, "platform_fee_per_order": 0.99, "note": "Moomoo (Malaysia) US stocks and ETFs; SEC/TAF/settlement fees not included"},
           "strategies": [results[s["id"]] for s in specs]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc))
    for b in BUDGETS:
        for rule in RULES:
            key = f"{int(b)}_{rule}"
            print(f"\n== account ${b:,.0f}, fee rule {rule}: top 10 by whole-period profit")
            for r in sorted(doc["strategies"], key=lambda r: -r["views"][key]["ret"])[:10]:
                v = r["views"][key]
                print(f"  {r['name']:<42} {v['ret']:+8.2f}%  trades {v['trades']:6.1f}  win {v['win']:5.1f}%  PF {v['pf']:5.2f}  fees {v['fees']:6.2f}%  | Aug-Oct {v['ret_final']:+7.2f}% ({v['trades_final']:.0f})")


if __name__ == "__main__":
    main()
