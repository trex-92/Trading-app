"""Serves backtest requests queued by the app: claim -> fetch bars -> run -> write results."""
import re
import threading
from datetime import date, datetime, timedelta

from .calendar import Calendar
from .runner import NAMES, run_backtest

TICKER_RE = re.compile(r"^[A-Z][A-Z.]{0,5}$")
MAX_DAYS = 90


def parse_request(strategy: str, params: dict, today: date | None = None) -> dict:
    today = today or date.today()
    if strategy not in NAMES:
        raise ValueError(f"unknown strategy {strategy!r}")
    tickers = [str(t).upper() for t in params.get("tickers") or ["SPY", "QQQ"]]
    if not 1 <= len(tickers) <= 3 or not all(TICKER_RE.match(t) for t in tickers):
        raise ValueError("tickers: 1 to 3 symbols like SPY, QQQ")
    start, end = date.fromisoformat(params["start"]), date.fromisoformat(params["end"])
    if end > today:
        end = today
    if start > end:
        raise ValueError("start must be before end")
    if (end - start).days > MAX_DAYS:
        raise ValueError(f"range too long (max {MAX_DAYS} days)")
    budget = float(params.get("budget") or 0)
    if not 0 < budget <= 1e9:
        raise ValueError("budget must be a positive amount")
    overrides = params.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")
    return {"tickers": tickers, "start": start, "end": end, "budget": budget, "overrides": overrides}


class BacktestWorker:
    def __init__(self, sync, bars_provider, calendar_path: str = "data/calendar.json", log=print):
        self.sync, self.bars, self.calendar_path, self.log = sync, bars_provider, calendar_path, log

    def poll_once(self) -> int:
        runs = self.sync.claim_backtests()
        for run in runs:
            self._handle(run)
        return len(runs)

    def _handle(self, run: dict) -> None:
        try:
            req = parse_request(run["strategy"], run.get("params") or {})
            data = {t: self.bars(t, req["start"] - timedelta(days=5), req["end"]) for t in req["tickers"]}
            if not any(data.values()):
                raise RuntimeError("no price data returned for that range (market holidays, or history not available)")
            res = run_backtest([run["strategy"]], data, req["budget"], req["overrides"], Calendar.load(self.calendar_path))
            res["request"] = {**{k: v for k, v in req.items() if k not in ("start", "end")},
                              "start": req["start"].isoformat(), "end": req["end"].isoformat()}
            summary = {"stats": res["stats"], "notes": res["notes"], "request": res["request"],
                       "trades_total": res["trades_total"]}
            self.sync.finish_backtest(run["id"], summary, res)
            self.log(f"[backtest] {run['id']} done: {res['stats'].get('n_trades')} trades")
        except Exception as e:  # noqa: BLE001 - report every failure to the app instead of dying
            self.log(f"[backtest] {run.get('id')} failed: {e}")
            self.sync.fail_backtest(run["id"], f"{type(e).__name__}: {e}")

    def run_forever(self, stop: threading.Event, interval: float = 5.0) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001
                self.log(f"[backtest] poll error: {e}")
            stop.wait(interval)
