"""Local backtests without the app:
  python -m bot.intraday.cli --strategy A --synthetic            (plumbing demo; says nothing about real markets)
  python -m bot.intraday.cli --strategy B --csv-dir data --tickers SPY,QQQ --budget 100000
CSV files: <csv-dir>/<TICKER>.csv with columns timestamp,open,high,low,close,volume (1-minute bars)."""
import argparse
import json
from datetime import date
from pathlib import Path

from .calendar import Calendar
from .data import business_days, load_csv, synthetic_history
from .market import get_market
from .runner import run_backtest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="ALL", help="A, B, C, or ALL")
    ap.add_argument("--market", default="US", help="US, SG or MY")
    ap.add_argument("--tickers", default=None, help="default: the market's sample symbols")
    ap.add_argument("--budget", type=float, default=100000)
    ap.add_argument("--csv-dir")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--calendar", default="data/calendar.json")
    ap.add_argument("--overrides", default="{}", help='JSON, e.g. \'{"A": {"t1_r": 2}}\'')
    ap.add_argument("--out", help="write the full JSON result here")
    a = ap.parse_args()
    market = get_market(a.market)
    tickers = [t.strip().upper() for t in (a.tickers or ",".join(market.default_symbols)).split(",")]
    if a.synthetic:
        data = synthetic_history(business_days(date(2026, 9, 1), date(2026, 9, 30)), ["trend", "range"], tickers, market=market)
        print("SYNTHETIC DATA: this only exercises the code. The numbers mean nothing.\n")
    elif a.csv_dir:
        data = {t: load_csv(Path(a.csv_dir) / f"{t}.csv", market.tz) for t in tickers}
    else:
        ap.error("give --csv-dir or --synthetic")
    codes = ["A", "B", "C"] if a.strategy.upper() == "ALL" else [a.strategy.upper()]
    res = run_backtest(codes, data, a.budget, json.loads(a.overrides), Calendar.load(a.calendar, market.code), market)
    print(json.dumps(res["stats"], indent=2))
    for n in res["notes"]:
        print("-", n)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
