import threading
from datetime import date

import pytest

from bot.intraday.data import business_days, synthetic_history
from bot.intraday.worker import BacktestWorker, parse_request

TODAY = date(2026, 10, 5)


class FakeSync:
    def __init__(self, runs):
        self.runs, self.done, self.failed = runs, {}, {}

    def claim_backtests(self):
        r, self.runs = self.runs, []
        return r

    def finish_backtest(self, rid, summary, result):
        self.done[rid] = (summary, result)

    def fail_backtest(self, rid, err):
        self.failed[rid] = err


def provider(sym, start, end):
    days = business_days(start, end)
    return synthetic_history(days, ["trend", "range"], [sym])[sym] if days else []


def req(**over):
    return {"tickers": ["SPY"], "start": "2026-09-01", "end": "2026-09-25", "budget": 50000, **over}


def test_parse_request_validation():
    assert parse_request("A", req(), TODAY)["tickers"] == ["SPY"]
    for bad in (req(tickers=["spy; drop table"]), req(start="2026-01-01"), req(budget=0),
                req(start="2026-09-30", end="2026-09-01"), req(overrides=[1]), {"start": "x"}):
        with pytest.raises((ValueError, KeyError)):
            parse_request("A", bad, TODAY)
    with pytest.raises(ValueError):
        parse_request("Z", req(), TODAY)
    assert parse_request("A", req(end="2030-01-01", start="2026-09-20"), TODAY)["end"] == TODAY  # clipped to today


def test_worker_runs_and_reports_results():
    sync = FakeSync([{"id": "r1", "strategy": "C", "params": req(tickers=["SPY", "QQQ"])}])
    assert BacktestWorker(sync, provider, "nope.json", log=lambda *a: None).poll_once() == 1
    summary, result = sync.done["r1"]
    assert summary["stats"]["n_trades"] == result["trades_total"] and result["request"]["budget"] == 50000
    assert result["data"]["SPY"]["days"] > 10 and any("no event calendar" in n for n in summary["notes"])


def test_worker_reports_failures_instead_of_crashing():
    sync = FakeSync([{"id": "bad", "strategy": "A", "params": req(overrides={"A": {"nope": 1}})},
                     {"id": "empty", "strategy": "A", "params": req()},
                     {"id": "inv", "strategy": "A", "params": req(budget=-5)}])
    BacktestWorker(sync, lambda *a: [] if a[0] == "SPY" and False else provider(*a), "x", log=lambda *a: None).poll_once()
    assert "unknown parameter" in sync.failed["bad"] and "budget" in sync.failed["inv"]
    sync2 = FakeSync([{"id": "empty", "strategy": "A", "params": req()}])
    BacktestWorker(sync2, lambda *a: [], "x", log=lambda *a: None).poll_once()
    assert "no price data" in sync2.failed["empty"]


def test_run_forever_stops():
    stop = threading.Event()
    stop.set()
    BacktestWorker(FakeSync([]), provider, "x", log=lambda *a: None).run_forever(stop)
