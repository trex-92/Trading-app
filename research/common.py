"""Shared pieces of the research harness: reads the cached 1-minute history (data/cache, filled by the bot's backtest
downloads or bot.smoke_moomoo) and exposes the split between the design period and the held-out period."""
import json, math, random, statistics, sys, time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from bot.intraday.backtest import Backtester                 # noqa: E402
from bot.intraday.calendar import Calendar                   # noqa: E402
from bot.intraday.data import bars_from_moomoo               # noqa: E402
from bot.intraday.market import US                           # noqa: E402
from bot.intraday.params import apply_overrides              # noqa: E402
from bot.intraday.runner import base_shared, run_backtest    # noqa: E402

CACHE = REPO / "data" / "cache"
# The split is set by set_split(); every analysis reads it through design() / holdout().
SPLIT = {"design_end": None}


def cached_symbols(start: date | None = None, end: date | None = None) -> dict[str, list[date]]:
    """{symbol: sorted weekdays that have a cache file} for the US market, optionally inside [start, end]."""
    out: dict[str, list[date]] = defaultdict(list)
    for f in CACHE.glob("US_*_????-??-??.json"):
        sym, day = f.stem[3:-11], date.fromisoformat(f.stem[-10:])
        if (start and day < start) or (end and day > end):
            continue
        out[sym].append(day)
    return {k: sorted(v) for k, v in out.items()}


def load_bars(symbols: list[str], start: date | None = None, end: date | None = None) -> dict:
    """Bars for `symbols` from the cache, optionally only the days in [start, end] (keeps a design window clean while
    other days are still being downloaded into the same cache)."""
    data = {}
    for t in symbols:
        rows = []
        for f in sorted(CACHE.glob(f"US_{t}_????-??-??.json")):
            d = date.fromisoformat(f.stem[-10:])
            if (start and d < start) or (end and d > end):
                continue
            rows += json.loads(f.read_text())
        data[t] = bars_from_moomoo(rows, US.tz)
    return data


def days_of(bars) -> list[date]:
    return sorted({b.ts.astimezone(US.tz).date() for b in bars if US.is_regular(b.ts)})


def pick_universe(min_share: float = 0.8, start: date | None = None, end: date | None = None):
    """(main symbols, unseen symbols): main = cached for at least `min_share` of the longest history; the rest are kept
    aside as a never-used check set."""
    cs = cached_symbols(start, end)
    longest = max(len(v) for v in cs.values())
    main = sorted(k for k, v in cs.items() if len(v) >= min_share * longest and len(v) > 5)
    unseen = sorted(k for k, v in cs.items() if k not in main and len(v) > 5)
    return main, unseen


def set_split(data: dict, holdout_share: float = 0.3, design_end: date | None = None) -> date:
    """Design = the earlier days, holdout = the last `holdout_share` of the tradable days (the first day only warms up)."""
    days = sorted({d for b in data.values() for d in days_of(b)})[1:]
    SPLIT["design_end"] = design_end or days[int(len(days) * (1 - holdout_share)) - 1]
    return SPLIT["design_end"]


def design(recs):  return [r for r in recs if r["day"] <= SPLIT["design_end"]]
def holdout(recs): return [r for r in recs if r["day"] > SPLIT["design_end"]]


def engine(factory, tickers, data, budget=100000.0, shared=None):
    """Run the repo's real Backtester (one position at a time, shared risk limits, fee rule) with a strategy object."""
    sh = apply_overrides(base_shared(US), shared or {})
    res = Backtester([factory()], sh, budget, Calendar(), market=US).run({t: data[t] for t in tickers})
    for t in res.trades:
        t["ret_pct"] = t["pnl"] / (t["shares"] * t["entry"]) * 100
        t["day"] = date.fromisoformat(t["date"])
    return res
