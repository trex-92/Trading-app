import threading
from datetime import date, timedelta

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


def provider(sym, start, end, market=None):
    from bot.intraday.market import US
    days = business_days(start, end)
    return synthetic_history(days, ["trend", "range"], [sym], market=market or US)[sym] if days else []


def req(**over):
    return {"tickers": ["SPY"], "start": "2026-09-01", "end": "2026-09-25", "budget": 50000, **over}


def test_parse_request_validation():
    assert parse_request("A", req(), TODAY)["tickers"] == ["SPY"]
    for bad in (req(tickers=["spy; drop table"]), req(start="2025-08-01"), req(budget=0),
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
    BacktestWorker(sync, provider, "x", log=lambda *a: None).poll_once()
    assert "unknown parameter" in sync.failed["bad"] and "budget" in sync.failed["inv"]
    sync2 = FakeSync([{"id": "empty", "strategy": "A", "params": req()}])
    BacktestWorker(sync2, lambda *a: [], "x", log=lambda *a: None).poll_once()
    assert "no price data" in sync2.failed["empty"]


def test_run_forever_stops():
    stop = threading.Event()
    stop.set()
    BacktestWorker(FakeSync([]), provider, "x", log=lambda *a: None).run_forever(stop)


def test_short_history_triggers_a_loud_warning():
    short = lambda sym, start, end, market=None: provider(sym, end - timedelta(days=1), end, market)  # noqa: E731
    sync = FakeSync([{"id": "short", "strategy": "A", "params": req(start="2026-09-01", end="2026-09-30")}])
    BacktestWorker(sync, short, "nope.json", log=lambda *a: None).poll_once()
    summary, result = sync.done["short"]
    assert summary["notes"][0].startswith("WARNING: only") and "short" not in summary["notes"][0][:7]
    assert "smoke_moomoo" in summary["notes"][0]
    full = FakeSync([{"id": "full", "strategy": "A", "params": req(start="2026-09-01", end="2026-09-25")}])
    BacktestWorker(full, provider, "nope.json", log=lambda *a: None).poll_once()
    assert not any("only" in n and "trading days" in n for n in full.done["full"][0]["notes"])


def test_up_to_ten_different_symbols_and_a_year_are_accepted():
    ten = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "AAPL", "MSFT"]
    r = parse_request("A", req(tickers=ten, start="2025-10-06", end="2026-09-25"), TODAY)
    assert r["tickers"] == ten
    for bad in (ten + ["SPY"], ["SPY", "SPY"], req(start="2025-09-01")["start"]):
        with pytest.raises((ValueError, TypeError, KeyError)):
            parse_request("A", req(tickers=bad) if isinstance(bad, list) else req(start=bad), TODAY)


def test_worker_reports_download_progress_per_symbol():
    class ProgressSync(FakeSync):
        def __init__(self, runs):
            super().__init__(runs)
            self.progress = []

        def progress_backtest(self, rid, text):
            self.progress.append(text)

    sync = ProgressSync([{"id": "p1", "strategy": "A", "params": req(tickers=["SPY", "QQQ", "IWM"])}])
    BacktestWorker(sync, provider, "nope.json", log=lambda *a: None).poll_once()
    assert [p.split(":")[1].split("(")[0].strip() for p in sync.progress[:3]] == ["SPY", "QQQ", "IWM"]
    assert "1 of 3" in sync.progress[0] and sync.progress[-1].startswith("Running the simulation")
    assert "p1" in sync.done


def test_unknown_symbols_are_named_before_anything_is_downloaded():
    downloads = []

    def tracking(sym, start, end, market=None):
        downloads.append(sym)
        return provider(sym, start, end, market)

    names = ["SPY", "MSFT", "ABBY", "KO", "QQQI"]
    sync = FakeSync([{"id": "bad", "strategy": "A", "params": req(tickers=names)}])
    check = lambda tickers, market: [t for t in tickers if t == "ABBY"]  # noqa: E731
    BacktestWorker(sync, tracking, "nope.json", log=lambda *a: None, symbol_check=check).poll_once()
    assert "ABBY" in sync.failed["bad"] and "SPY" not in sync.failed["bad"] and "Nothing was downloaded" in sync.failed["bad"]
    assert downloads == []                                              # failed fast: no rate-limited downloads wasted
    ok = FakeSync([{"id": "ok", "strategy": "A", "params": req(tickers=["SPY", "KO"])}])
    BacktestWorker(ok, tracking, "nope.json", log=lambda *a: None, symbol_check=lambda t, m: []).poll_once()
    assert "ok" in ok.done and downloads == ["SPY", "KO"]
