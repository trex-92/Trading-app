import json
from datetime import date, datetime, time, timedelta

import pytest
from test_intraday import D1, D2, Fixed, mkday

from bot.intraday.bars import NY
from bot.intraday.live import LiveRunner
from bot.intraday.params import SharedParams
from bot.models import Order, Position


class Clock:
    now = datetime(2026, 9, 2, 9, 30, 3, tzinfo=NY)


class FakeBroker:
    def __init__(self, bars, clock):
        self.bars, self.clock = bars, clock
        self.stale = self.reject = self.no_fill = False
        self.qty, self.avg, self.orders, self.n = {}, {}, {}, 0
        self.placed = []

    def _done(self, t):
        return [b for b in self.bars[t] if b.ts + timedelta(minutes=1) <= self.clock.now]

    def intraday_bars(self, t, start, end):
        return [b for b in self._done(t) if start <= b.ts.date() <= end]

    def recent_bars(self, t, n=15):
        return self._done(t)[-n:]

    def snapshot(self, syms):
        ts = self.clock.now - timedelta(seconds=60 if self.stale else 1)
        return {s: {"last": self._done(s)[-1].close, "bid": 0, "ask": 0, "ts": ts} for s in syms}

    def positions(self):
        return [Position(s, q, self.avg[s], 0) for s, q in self.qty.items() if q]

    def cash(self): return 1_000_000.0
    def equity(self): return 1_000_000.0

    def place_order(self, order):
        self.n += 1
        order.id = f"o{self.n}"
        self.placed.append((order.side, order.qty, order.price))
        if self.reject:
            order.status, order.reason = "REJECTED", "test reject"
            return order
        last = self._done(order.symbol)[-1].close
        marketable = order.price >= last if order.side == "BUY" else order.price <= last
        rec = {"status": "OPEN", "filled_qty": 0.0, "avg_price": 0.0, "symbol": order.symbol, "side": order.side}
        if marketable and not self.no_fill:
            sign = 1 if order.side == "BUY" else -1
            self.qty[order.symbol] = self.qty.get(order.symbol, 0) + sign * order.qty
            self.avg[order.symbol] = last
            rec.update(status="FILLED", filled_qty=float(order.qty), avg_price=last)
        self.orders[order.id] = rec
        order.status = "SUBMITTED"
        return order

    def order_status(self, oid): return dict(self.orders[oid])

    def open_orders(self):
        return [{"id": k, "symbol": v["symbol"], "side": v["side"]} for k, v in self.orders.items() if v["status"] == "OPEN"]

    def cancel_order(self, oid):
        if self.orders[oid]["status"] == "OPEN":
            self.orders[oid]["status"] = "CANCELLED"


class FakeSync:
    def __init__(self): self.trades, self.status = [], []
    def push_trade(self, t): self.trades.append(t)
    def push_engine_status(self, s, market='US'): self.status.append(s)


def make(tmp_path, over=None, at=(40,), budget=100000, enabled=True, broker_cls=FakeBroker, fx=None,
         configs_shared=None, seen=None, **kw):
    clock = Clock()
    broker = broker_cls({"SPY": mkday(D1) + mkday(D2, over)}, clock)
    sync, logs = FakeSync(), []
    params = {"shared": configs_shared} if configs_shared is not None else {}
    cfg = lambda: [{"strategy": "A", "enabled": enabled, "budget": budget, "params": dict(params)}]  # noqa: E731

    def factory(codes, ov):
        if seen is not None:
            seen["params"] = ov.get("A", {})
        return [Fixed(at, name="A", **(fx or {}))]

    kw.setdefault("strategy_factory", factory)
    runner = LiveRunner(broker, sync, universe=["SPY"], configs=cfg, calendar_path=str(tmp_path / "cal.json"),
                        state_path=str(tmp_path / "state.json"), journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now,
                        sleep=lambda s: None, log=logs.append, fill_timeout=0.01, **kw)
    return runner, broker, sync, clock, logs


def drive(runner, clock, broker, first, last):
    for m in range(first, last + 1):
        clock.now = datetime(2026, 9, 2, 9, 30, tzinfo=NY) + timedelta(minutes=m + 1, seconds=3)
        runner.cycle()


def test_real_accounts_are_refused(tmp_path):
    with pytest.raises(RuntimeError, match="paper-only"):
        make(tmp_path, real=True)


def test_entry_then_stop_exit_is_journaled(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, {45: (100, 100.1, 98.8, 98.9), 46: (98.9, 99, 98.8, 98.9)})
    drive(r, clock, b, 0, 47)
    assert b.placed[0] == ("BUY", 1000, 100.0)
    t = sync.trades[0]
    assert t["exits"][0]["reason"] == "stop" and t["shares"] == 1000 and t["mode"] == "paper"
    assert t["pnl"] == pytest.approx((98.9 - 100) * 1000 - 20) and t["r"] < -1
    assert r.pos is None and r.risk.consecutive_losses == 1
    assert json.loads((tmp_path / "j.jsonl").read_text().splitlines()[0])["ticker"] == "SPY"


def test_target1_partial_then_breakeven_exit(tmp_path):
    over = {45: (100, 101.7, 99.9, 101.6), 46: (101.6, 101.6, 99.9, 100.0)}
    r, b, sync, clock, logs = make(tmp_path, over)
    drive(r, clock, b, 0, 47)
    assert [e["reason"] for e in sync.trades[0]["exits"]] == ["target1", "breakeven_stop"]
    assert [e["qty"] for e in sync.trades[0]["exits"]] == [500, 500]


def test_budget_sets_position_size(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, budget=10000)
    drive(r, clock, b, 0, 41)
    assert b.placed[0][1] == 100  # 1% of 10,000 / $1 risk


def test_disabled_or_unfunded_strategy_never_trades(tmp_path):
    for kw in ({"enabled": False}, {"budget": 0}):
        r, b, sync, clock, logs = make(tmp_path / ("x" + str(len(kw))), **kw)
        drive(r, clock, b, 0, 60)
        assert b.placed == []


def test_unfilled_entry_is_cancelled_and_no_position(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    b.no_fill = True
    drive(r, clock, b, 0, 45)
    assert r.pos is None and b.open_orders() == [] and any("not filled" in m for m in logs)


def test_stale_feed_blocks_entries_then_recovers(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, at=(40, 60))
    b.stale = True
    drive(r, clock, b, 0, 45)
    assert b.placed == [] and any("FEED STALE" in m for m in logs) and r.feed_ok is False
    b.stale = False
    drive(r, clock, b, 46, 62)
    assert b.placed and r.feed_ok  # the second signal (minute 60) is taken once the feed is back


def test_rejected_order_halts_entries_until_restart(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, at=(40, 60))
    b.reject = True
    drive(r, clock, b, 0, 41)
    assert r.halt_reason and "rejected" in r.halt_reason
    b.reject = False
    drive(r, clock, b, 42, 62)
    assert len(b.placed) == 1  # the second signal was refused by the kill switch


def test_app_halt_blocks_entries_but_exits_still_work(tmp_path):
    state = {"halt": False}
    r, b, sync, clock, logs = make(tmp_path, {45: (100, 100.1, 98.8, 98.9)}, halted=lambda: state["halt"])
    drive(r, clock, b, 0, 41)
    assert r.pos
    state["halt"] = True
    drive(r, clock, b, 42, 47)
    assert r.pos is None and sync.trades  # position closed by its stop even while halted


def test_time_stop_exit_on_bar_close(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, fx={"time_stop": 25})
    drive(r, clock, b, 0, 70)
    assert sync.trades[0]["exits"][0]["reason"] == "time_stop"


def test_cycle_does_nothing_outside_the_session(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    clock.now = datetime(2026, 9, 2, 17, 0, tzinfo=NY)
    r.cycle()
    assert b.placed == [] and sync.status[-1]["session"] == "closed"
    clock.now = datetime(2026, 9, 5, 11, 0, tzinfo=NY)  # Saturday
    r.cycle()
    assert b.placed == []


def test_fomc_day_is_not_traded(tmp_path):
    (tmp_path / "cal.json").write_text(json.dumps({"fomc_days": [D2.isoformat()]}))
    r, b, sync, clock, logs = make(tmp_path)
    drive(r, clock, b, 0, 50)
    assert b.placed == [] and r.status("open")["blocked_today"] == "fomc_day"


def test_restarting_mid_day_does_not_trade_old_signals(tmp_path):
    r, b, sync, clock, logs = make(tmp_path, at=(40,))
    drive(r, clock, b, 100, 102)  # first cycle happens at 11:11; the minute-40 signal is history
    assert b.placed == []


def test_reconcile_halts_on_unexpected_broker_position_and_cancels_leftovers(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    b.qty["SPY"], b.avg["SPY"] = 10, 99.0
    b.orders["old"] = {"status": "OPEN", "filled_qty": 0, "avg_price": 0, "symbol": "SPY", "side": "BUY"}
    drive(r, clock, b, 0, 45)
    assert "unexpected 10 SPY" in r.halt_reason and b.orders["old"]["status"] == "CANCELLED"
    assert [p for p in b.placed if p[0] == "BUY"] == []


def test_restart_resumes_a_saved_position_and_keeps_managing_it(tmp_path):
    over = {45: (100, 100.1, 98.8, 98.9)}
    r, b, sync, clock, logs = make(tmp_path, over)
    drive(r, clock, b, 0, 42)
    assert r.pos and r.pos["remaining"] == 1000
    # "restart": a brand-new runner on the same state file and broker
    r2 = LiveRunner(b, sync, universe=["SPY"], configs=lambda: [{"strategy": "A", "enabled": True, "budget": 100000, "params": {}}],
                    calendar_path=str(tmp_path / "cal.json"), state_path=str(tmp_path / "state.json"),
                    journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now, sleep=lambda s: None, log=logs.append,
                    fill_timeout=0.01, strategy_factory=lambda codes, ov: [Fixed([], name="A")])
    drive(r2, clock, b, 43, 47)
    assert r2.halt_reason is None and sync.trades and sync.trades[0]["exits"][0]["reason"] == "stop"


def test_restart_with_different_broker_quantity_halts(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    drive(r, clock, b, 0, 42)
    b.qty["SPY"] = 123  # someone changed the position while the bot was down
    r2 = LiveRunner(b, sync, universe=["SPY"], configs=lambda: [{"strategy": "A", "enabled": True, "budget": 100000, "params": {}}],
                    calendar_path=str(tmp_path / "cal.json"), state_path=str(tmp_path / "state.json"),
                    journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now, sleep=lambda s: None, log=logs.append,
                    fill_timeout=0.01, strategy_factory=lambda codes, ov: [Fixed([], name="A")])
    drive(r2, clock, b, 43, 44)
    assert "mismatch" in r2.halt_reason


def test_status_is_published_with_honest_stop_note(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    drive(r, clock, b, 0, 42)
    s = sync.status[-1]
    assert s["mode"] == "paper" and "held by the bot" in s["stops"] and s["enabled"] == ["A"] and s["position"]["ticker"] == "SPY"


def test_breaker_level_1_halves_position_size_and_is_persisted(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    r.guard.record(-7000, 100000)  # 7% drawdown => level 1
    assert r.guard.level == 1
    drive(r, clock, b, 0, 41)
    assert b.placed[0][1] == 500   # 0.5% risk instead of 1%
    assert json.loads((tmp_path / "state.json").read_text())["guard"]["level"] == 1
    assert sync.status[-1]["risk"] == {"per_trade_pct": 0.5, "breaker_level": 1, "live_disabled": False, "daily_max_loss_pct": 2.0}


def test_engine_refuses_to_start_above_the_risk_ceiling(tmp_path):
    with pytest.raises(ValueError, match="refusing to start"):
        make(tmp_path, shared=SharedParams(risk_per_trade_pct=1.5))


def test_daily_loss_limit_uses_two_percent_of_enabled_budgets(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    drive(r, clock, b, 0, 1)
    assert r.risk.start_equity == 100000
    r.risk.record(-2000)
    assert "daily loss" in r.risk.can_enter(r.shared)


def test_live_engine_matches_the_backtest_on_the_same_prices(tmp_path):
    from bot.intraday.backtest import Backtester
    from bot.intraday.data import business_days, synthetic_history
    from bot.intraday.runner import build

    days = business_days(date(2026, 9, 1), date(2026, 9, 4))
    data = synthetic_history(days, ["trend"], ["SPY"])
    last = days[-1]
    clock = Clock()
    clock.now = datetime(last.year, last.month, last.day, 9, 30, 3, tzinfo=NY)
    broker, sync = FakeBroker(data, clock), FakeSync()
    cfg = lambda: [{"strategy": c, "enabled": True, "budget": 100000, "params": {}} for c in ("A", "B")]  # noqa: E731
    r = LiveRunner(broker, sync, universe=["SPY"], configs=cfg, calendar_path=str(tmp_path / "c.json"),
                   state_path=str(tmp_path / "s.json"), journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now,
                   sleep=lambda s: None, log=lambda m: None, fill_timeout=0.01)
    for m in range(390):
        clock.now = datetime(last.year, last.month, last.day, 9, 30, tzinfo=NY) + timedelta(minutes=m + 1, seconds=3)
        r.cycle()
    strategies, shared = build(["A", "B"], None)
    bt = [t for t in Backtester(strategies, shared, 100000).run(data).trades if t["date"] == last.isoformat()]
    sig = lambda t: (t["strategy"], t["entry_ts"][11:16], round(t["entry"], 2), [e["reason"] for e in t["exits"]])  # noqa: E731
    assert sync.trades and [sig(t) for t in sync.trades] == [sig(t) for t in bt]
    assert sync.trades[0]["r"] == pytest.approx(bt[0]["r"], abs=0.1)
    assert r.pos is None


def _fx_runner(tmp_path, shared, **kw):
    seen = {}
    r, b, sync, clock, logs = make(tmp_path, configs_shared=shared, seen=seen, **kw)
    return r, b, sync, clock, logs, seen


def test_app_limits_reach_sizing_and_fractional_positions_are_managed_to_the_end(tmp_path):
    over = {45: (100, 101.7, 99.9, 101.6), 46: (101.6, 101.6, 99.9, 100.0)}
    r, b, sync, clock, logs, seen = _fx_runner(tmp_path, {"max_trade_notional": 50, "allow_fractional": True}, over=over)
    drive(r, clock, b, 0, 47)
    assert b.placed[0] == ("BUY", 0.5, 100.0)                             # $50 cap at $100 = half a share
    t = sync.trades[0]
    assert t["shares"] == 0.5 and [(e["reason"], e["qty"]) for e in t["exits"]] == [("target1", 0.25), ("breakeven_stop", 0.25)]
    assert r.pos is None
    assert "shared" not in seen["params"]                                 # limits are not passed on as strategy parameters


def test_invalid_app_limits_skip_the_signal_instead_of_trading(tmp_path):
    r, b, sync, clock, logs, seen = _fx_runner(tmp_path, {"max_trade_notional": -5})
    drive(r, clock, b, 0, 45)
    assert b.placed == [] and any("invalid limits" in m for m in logs)


def test_restart_resumes_a_fractional_position(tmp_path):
    r, b, sync, clock, logs, seen = _fx_runner(tmp_path, {"max_trade_notional": 50, "allow_fractional": True},
                                               over={45: (100, 100.1, 98.8, 98.9)})
    drive(r, clock, b, 0, 42)
    assert r.pos and r.pos["remaining"] == 0.5
    r2 = LiveRunner(b, sync, universe=["SPY"], configs=lambda: [{"strategy": "A", "enabled": True, "budget": 100000, "params": {}}],
                    calendar_path=str(tmp_path / "cal.json"), state_path=str(tmp_path / "state.json"),
                    journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now, sleep=lambda s: None, log=logs.append,
                    fill_timeout=0.01, strategy_factory=lambda codes, ov: [Fixed([], name="A")])
    drive(r2, clock, b, 43, 47)
    assert r2.halt_reason is None and sync.trades and sync.trades[0]["shares"] == 0.5


def test_history_is_loaded_before_the_open_and_trading_starts_normally(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    clock.now = datetime(2026, 9, 2, 8, 50, tzinfo=NY)               # 40 minutes before the bell
    r.cycle()
    assert r.day == D2 and any("new day" in m for m in logs) and b.placed == []
    assert sync.status[-1]["session"] == "closed"                      # still reported as closed
    loaded = [m for m in logs if "new day" in m]
    drive(r, clock, b, 0, 41)                                           # the open: no second set-up, the signal is taken
    assert [m for m in logs if "new day" in m] == loaded and b.placed and b.placed[0][0] == "BUY"


def test_nothing_is_loaded_long_before_the_open_or_on_weekends(tmp_path):
    r, b, sync, clock, logs = make(tmp_path)
    clock.now = datetime(2026, 9, 2, 6, 0, tzinfo=NY)                  # 3.5 hours early
    r.cycle()
    assert r.day is None
    clock.now = datetime(2026, 9, 5, 9, 0, tzinfo=NY)                  # Saturday
    r.cycle()
    assert r.day is None
