"""Download and cache 1-minute US history for a date range into data/cache. Read-only quote calls; days already in the cache
are skipped, finished days are saved as they arrive, so the script can be stopped and restarted at any time.

    python -m research.download --symbols KO,MU,IBM --start 2026-01-01 --end 2026-10-02

Needs a Moomoo login on this machine (python -m bot.moomoo_login). Moomoo rate-limits history requests, so a long range is slow
(roughly one request per trading day per symbol)."""
import argparse
import time
from datetime import date, timedelta

from .common import CACHE, REPO  # noqa: F401  (REPO puts the repo on sys.path)
from bot.brokers.moomoo_rest import MoomooError, MoomooRestBroker
from bot.intraday.market import US
from bot.moomoo_oauth import AuthError


def missing_weekdays(sym: str, start: date, end: date) -> list[date]:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if d.weekday() < 5 and not (CACHE / f"US_{sym}_{d.isoformat()}.json").exists()]


def blocks(days: list[date], size: int) -> list[list[date]]:
    """Split missing weekdays into runs of at most `size` that are contiguous (a weekend does not break a run), so a request never
    re-reads cached days that lie between two missing ones."""
    out: list[list[date]] = []
    for d in days:
        if out and (d - out[-1][-1]).days <= 3 and len(out[-1]) < size:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--chunk-days", type=int, default=45, help="weekdays per request series (the pager is capped at 150 pages)")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    broker = MoomooRestBroker("SIMULATE", "", cache_dir=str(CACHE))
    t0, failed = time.time(), []
    for sym in [s.strip().upper() for s in a.symbols.split(",")]:
        todo = missing_weekdays(sym, start, end)
        print(f"{sym}: {len(todo)} weekdays not cached yet", flush=True)
        for block in blocks(todo, a.chunk_days):
            for attempt in range(1, 4):
                try:
                    bars = broker.intraday_bars(sym, block[0], block[-1], US)
                    days = len({b.ts.astimezone(US.tz).date() for b in bars if US.is_regular(b.ts)})
                    print(f"  {sym} {block[0]} -> {block[-1]}: {len(bars)} bars, {days} trading days  [{(time.time() - t0) / 60:.1f} min elapsed]", flush=True)
                    break
                except (MoomooError, AuthError, OSError) as e:
                    print(f"  {sym} {block[0]} -> {block[-1]}: attempt {attempt} failed: {e}", flush=True)
                    if attempt == 3:
                        failed.append((sym, block[0], block[-1]))
                    else:
                        time.sleep(30)
    left = {s: len(missing_weekdays(s, start, end)) for s in [x.strip().upper() for x in a.symbols.split(",")]}
    print("\nDONE" if not failed else f"\nFINISHED WITH FAILURES: {failed}", f"| weekdays still not cached: {left}", flush=True)


if __name__ == "__main__":
    main()
