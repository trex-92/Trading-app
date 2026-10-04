import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.brokers.local_paper import LocalPaperBroker
from bot.intraday.backtest import Backtester
from bot.intraday.bars import Bar
from bot.intraday.calendar import Calendar
from bot.intraday.context import DayContext, TickerState
from bot.intraday.data import business_days, synthetic_day, synthetic_history
from bot.intraday.market import MY, SG, US, get_market
from bot.intraday.params import SharedParams
from bot.intraday.risk import cost_per_share, size_position
from bot.intraday.runner import base_shared, run_backtest
from bot.intraday.strategies import Range, Scalp, Trend
from bot.intraday.worker import parse_request
from bot.models import Order

D = date(2026, 9, 2)  # a Wednesday


# ---- sessions ---------------------------------------------------------------------------------------------------
def test_session_arithmetic_skips_the_lunch_break():
    assert (US.total_minutes, SG.total_minutes, MY.total_minutes) == (390, 420, 360)
    at = lambda h, m: datetime(2026, 9, 2, h, m, tzinfo=MY.tz)  # noqa: E731
    assert MY.minute_of_session(at(9, 0)) == 0 and MY.minute_of_session(at(12, 29)) == 209
    assert MY.minute_of_session(at(12, 30)) is None and MY.minute_of_session(at(14, 29)) is None
    assert MY.minute_of_session(at(14, 30)) == 210 and MY.minute_of_session(at(16, 59)) == 359
    assert MY.minute_of_session(at(17, 0)) is None and MY.minute_of_session(at(8, 59)) is None
    for m in (0, 209, 210, 359):
        assert MY.minute_of_session(MY.ts_at(D, m)) == m
    with pytest.raises(ValueError):
        MY.ts_at(D, 360)


def test_timezones_are_applied_automatically():
    # 09:30 New York in September is 21:30 in Kuala Lumpur (DST handled by the tz database, not by us)
    ny_open = datetime(2026, 9, 2, 13, 30, tzinfo=ZoneInfo("UTC"))
    assert US.minute_of_session(ny_open) == 0
    assert US.minute_of_session(datetime(2026, 12, 2, 14, 30, tzinfo=ZoneInfo("UTC"))) == 0  # winter: one hour later in UTC
    assert MY.minute_of_session(datetime(2026, 9, 2, 1, 0, tzinfo=ZoneInfo("UTC"))) == 0      # 09:00 MYT = 01:00 UTC


def test_market_overrides_and_validation(tmp_path):
    f = tmp_path / "markets.json"
    f.write_text(json.dumps({"SG": {"sessions": [["09:00", "17:00"]], "cost_pct_round_trip": 0.05, "lot_size": 100}}))
    sg = get_market("sg", f)
    assert sg.total_minutes == 480 and sg.cost_pct_round_trip == 0.05 and sg.minute_of_session(datetime(2026, 9, 2, 12, 30, tzinfo=sg.tz)) == 210
    f.write_text(json.dumps({"MY": {"nope": 1}}))
    with pytest.raises(ValueError, match="cannot override"):
        get_market("MY", f)
    f.write_text(json.dumps({"MY": {"sessions": [["09:00", "12:33"]]}}))
    with pytest.raises(ValueError, match="5-minute"):
        get_market("MY", f)
    with pytest.raises(ValueError, match="unknown market"):
        get_market("HK")


# ---- context across a lunch break ---------------------------------------------------------------------------------
def test_5m_bars_never_straddle_the_lunch_break():
    ctx = DayContext("1155", D, TickerState(), market=MY)
    bars = synthetic_day(D, "trend", 1, 10.0, MY)
    assert len(bars) == 360 and bars[209].ts.hour == 12 and bars[210].ts.hour == 14
    closes = [i for i, b in enumerate(bars) if ctx.add_1m(b)]
    assert len(ctx.bars5) == 72 and closes[0] == 4 and 209 in closes and 214 in closes
    assert all(b.ts.minute % 5 == 0 for b in ctx.bars5)
    assert ctx.bars5[41].ts.hour == 12 and ctx.bars5[42].ts.hour == 14 and ctx.bars5[42].ts.minute == 30
    assert ctx.elapsed == 360 and ctx.ib_ready and ctx.or_ready
    with pytest.raises(ValueError, match="outside MY trading hours"):
        ctx.add_1m(Bar(datetime(2026, 9, 2, 13, 0, tzinfo=MY.tz), 1, 1, 1, 1, 1))


# ---- strategies on the local markets -----------------------------------------------------------------------------
def test_strategies_trade_in_my_and_sg_with_whole_lots_when_costs_are_negligible():
    days = business_days(date(2026, 9, 1), date(2026, 9, 15))
    for market in (MY, SG):
        trend = synthetic_history(days, ["trend"], ["AAA"], market=market)
        rng = synthetic_history(days, ["range"], ["AAA"], market=market)
        for code, data in (("A", trend), ("B", trend), ("C", rng)):
            res = run_backtest([code], data, 1_000_000, {"shared": {"cost_pct_round_trip": 0.01}}, None, market)
            assert res["stats"]["n_trades"] > 0, (market.code, code)
            for t in res["trades"]:
                assert t["shares"] % 100 == 0 and t["market"] == market.code
                assert all(e["qty"] % 100 == 0 for e in t["exits"])
                assert sum(e["qty"] for e in t["exits"]) == t["shares"]
            assert res["market"] == market.code and res["currency"] == market.currency


def test_placeholder_local_fees_make_these_tight_stop_strategies_untradeable():
    """The spec skips a trade when costs exceed 10% of 1R. At ~0.3% round-trip fees that needs R of 3% or more."""
    days = business_days(date(2026, 9, 1), date(2026, 9, 15))
    trend = synthetic_history(days, ["trend"], ["AAA"], market=MY)
    rng = synthetic_history(days, ["range"], ["AAA"], market=MY)
    for code, data in (("A", trend), ("B", trend), ("C", rng)):
        res = run_backtest([code], data, 1_000_000, None, None, MY)
        assert res["stats"]["n_trades"] == 0 and res["skipped"].get("cost", 0) > 0, code
    # explicit proof of the arithmetic: R = 1% of price, fees 0.3% => 30% of 1R > 10%
    s = size_position(1_000_000, 10.0, 9.9, base_shared(MY))
    assert "cost" in s["skip"] and cost_per_share(10.0, base_shared(MY)) == pytest.approx(0.03)


def test_lot_rounding_and_single_lot_full_exit():
    p = SharedParams(lot_size=100, cost_pct_round_trip=0.0, cost_per_share_round_trip=0.0)
    s = size_position(1_000_000, 10.0, 9.0, p)           # by risk 10,000 sh, by notional 100,000 -> 10,000
    assert s["shares"] == 10_000 and s["shares"] % 100 == 0
    s = size_position(5_000, 10.0, 9.9, p)               # by risk 500, by notional 500 -> exactly 5 lots
    assert s["shares"] == 500
    assert size_position(900, 10.0, 9.0, p)["skip"] == "shares < 1"   # cannot afford one lot of 100 (needs RM1,000)


# ---- calendar per market -----------------------------------------------------------------------------------------
def test_calendar_layouts(tmp_path):
    f = tmp_path / "cal.json"
    f.write_text(json.dumps({"fomc_days": ["2026-09-02"]}))                   # legacy flat layout = US only
    assert Calendar.load(f, "US").blocked(D) == "fomc_day"
    assert Calendar.load(f, "MY").configured is False
    f.write_text(json.dumps({"markets": {"MY": {"blocked_days": ["2026-09-02"], "half_days": ["2026-09-03"]}}}))
    my = Calendar.load(f, "MY")
    assert my.blocked(D) == "blocked_day" and my.blocked(date(2026, 9, 3)) == "half_day" and my.configured
    assert Calendar.load(f, "US").configured is False and Calendar.load(f, "SG").configured is False


# ---- worker requests -----------------------------------------------------------------------------------------------
def test_backtest_request_accepts_market_specific_symbols():
    base = {"start": "2026-09-01", "end": "2026-09-25", "budget": 50000}
    r = parse_request("A", {**base, "market": "MY", "tickers": ["1155", "5225"]}, date(2026, 10, 5))
    assert r["market"] == "MY" and r["tickers"] == ["1155", "5225"]
    assert parse_request("A", {**base, "market": "SG"}, date(2026, 10, 5))["tickers"] == ["ES3"]
    assert parse_request("A", base, date(2026, 10, 5))["market"] == "US"
    with pytest.raises(ValueError, match="unknown market"):
        parse_request("A", {**base, "market": "HK"}, date(2026, 10, 5))
    with pytest.raises(ValueError, match="symbols"):
        parse_request("A", {**base, "market": "US", "tickers": ["1155"]}, date(2026, 10, 5))


# ---- bot-side simulated account (no Moomoo simulator for SG/MY) -------------------------------------------------------
class Quotes:
    def __init__(self): self.q = {"1155": {"last": 10.0, "bid": 9.99, "ask": 10.01, "ts": None}}
    def snapshot(self, symbols, market=None): return {s: self.q[s] for s in symbols}
    def intraday_bars(self, *a, **k): return []
    def recent_bars(self, *a, **k): return []


def test_local_paper_broker_fills_against_bid_and_ask(tmp_path):
    q = Quotes()
    b = LocalPaperBroker(q, MY, tmp_path / "p.json", 100_000)
    o = b.place_order(Order("1155", "BUY", 1000, price=10.02))
    assert o.status == "SUBMITTED" and b.order_status(o.id) == {"status": "FILLED", "filled_qty": 1000.0, "avg_price": 10.01}
    assert b.cash() == pytest.approx(100_000 - 10_010) and [(p.symbol, p.qty) for p in b.positions()] == [("1155", 1000)]
    s = b.place_order(Order("1155", "SELL", 400, price=9.98))
    assert b.order_status(s.id)["avg_price"] == 9.99          # sold at the bid, paying the spread
    assert b.positions()[0].qty == 600


def test_local_paper_broker_waits_cancels_and_rejects(tmp_path):
    q = Quotes()
    b = LocalPaperBroker(q, MY, tmp_path / "p.json", 100_000)
    o = b.place_order(Order("1155", "BUY", 100, price=9.50))      # below the ask: rests
    assert b.order_status(o.id)["status"] == "OPEN" and [x["id"] for x in b.open_orders()] == [o.id]
    q.q["1155"]["ask"] = 9.45                                      # market comes to us
    assert b.order_status(o.id)["status"] == "FILLED"
    o2 = b.place_order(Order("1155", "BUY", 100, price=5.0))
    b.cancel_order(o2.id)
    assert b.order_status(o2.id)["status"] == "CANCELLED" and b.open_orders() == []
    for bad in (Order("1155", "BUY", 150, price=10.1), Order("1155", "SELL", 500, price=9.0),
                Order("1155", "BUY", 100_000, price=10.1)):
        assert b.place_order(bad).status == "REJECTED", bad
    assert b.place_order(Order("1155", "BUY", 100, price=None)).status == "REJECTED"


def test_local_paper_broker_state_survives_a_restart(tmp_path):
    q = Quotes()
    LocalPaperBroker(q, MY, tmp_path / "p.json", 100_000).place_order(Order("1155", "BUY", 200, price=10.02))
    b2 = LocalPaperBroker(q, MY, tmp_path / "p.json", 999)   # starting_cash is ignored once state exists
    assert b2.positions()[0].qty == 200 and b2.cash() == pytest.approx(100_000 - 2002)
    assert b2.equity() == pytest.approx(b2.cash() + 200 * 10.0)


# ---- the live engine across a lunch break ------------------------------------------------------------------------
def _my_flat_day(day, overrides=None):
    overrides = overrides or {}
    return [Bar(MY.ts_at(day, m), *overrides.get(m, (10.0, 10.005, 9.995, 10.0)), 1000) for m in range(MY.total_minutes)]


def _my_runner(tmp_path, strategy, **kw):
    from test_live import Clock, FakeBroker, FakeSync
    from bot.intraday.live import LiveRunner
    prev = D - timedelta(days=1)
    clock = Clock()
    broker = FakeBroker({"1155": _my_flat_day(prev) + _my_flat_day(D, kw.pop("over", None))}, clock)
    sync = FakeSync()
    cfg = lambda: [{"strategy": "A", "enabled": True, "budget": 1_000_000, "params": {}}]  # noqa: E731
    r = LiveRunner(broker, sync, universe=["1155"], configs=cfg, calendar_path=str(tmp_path / "c.json"),
                   state_path=str(tmp_path / "s.json"), journal_path=str(tmp_path / "j.jsonl"), clock=lambda: clock.now,
                   sleep=lambda s: None, log=lambda m: None, fill_timeout=0.01, market=MY,
                   strategy_factory=lambda codes, ov: [strategy], **kw)
    return r, broker, sync, clock


def _drive_my(r, clock, first, last):
    for m in range(first, last + 1):
        clock.now = MY.ts_at(D, m) + timedelta(minutes=1, seconds=3)
        r.cycle()


def test_live_engine_idles_during_lunch_and_counts_only_trading_minutes(tmp_path):
    from test_intraday import Fixed
    r, b, sync, clock = _my_runner(tmp_path, Fixed([200], name="A", time_stop=25, flat_min=MY.total_minutes - 5))
    _drive_my(r, clock, 0, 205)
    assert r.pos and r.pos["entry_elapsed"] == 201 and b.placed[0][0] == "BUY" and b.placed[0][1] == 10_000
    clock.now = datetime(2026, 9, 2, 13, 30, tzinfo=MY.tz)       # lunch: market closed
    n = len(b.placed)
    r.cycle()
    assert len(b.placed) == n and r.status("x")["market"] == "MY" and r.pos
    _drive_my(r, clock, 210, 240)
    t = sync.trades[0]
    exit_time = datetime.fromisoformat(t["exits"][0]["ts"])
    # 25 TRADING minutes after entry_elapsed=201 is elapsed 226, i.e. 14:46 (not 12:46: lunch does not count)
    assert t["exits"][0]["reason"] == "time_stop" and (exit_time.hour, exit_time.minute) == (14, 46)
    assert t["market"] == "MY" and t["shares"] % 100 == 0


def test_live_engine_flattens_before_the_malaysian_close(tmp_path):
    from test_intraday import Fixed
    r, b, sync, clock = _my_runner(tmp_path, Fixed([340], name="A", flat_min=MY.total_minutes - 5))
    _drive_my(r, clock, 0, 359)
    exit_time = datetime.fromisoformat(sync.trades[0]["exits"][0]["ts"])
    assert sync.trades[0]["exits"][0]["reason"] == "flat_eod" and (exit_time.hour, exit_time.minute) == (16, 55)


def test_market_switch_off_blocks_entries_but_not_exits(tmp_path):
    from test_intraday import Fixed
    r, b, sync, clock = _my_runner(tmp_path, Fixed([40, 100], name="A"), over={44: (10, 10.01, 8.8, 8.9), 45: (8.9, 8.9, 8.8, 8.9)})
    _drive_my(r, clock, 0, 41)
    assert r.pos
    r.enabled = False
    _drive_my(r, clock, 42, 110)
    assert sync.trades and sync.trades[0]["exits"][0]["reason"] == "stop"     # managed while switched off
    assert len([p for p in b.placed if p[0] == "BUY"]) == 1                  # the minute-100 signal was refused


def test_moomoo_symbol_prefix_and_timezone_per_market(tmp_path):
    from test_moomoo_rest import broker, ok_sim
    seen = {}

    def h(req):
        if req.url.path.endswith("/quote/snapshot"):
            seen["codes"] = __import__("json").loads(req.content)["code_list"]
            return ok_sim({"snapshot_list": [{"code": "MY.1155", "last_price": 10.0, "update_time": 1790000000000}]})
        raise AssertionError(req.url.path)

    b = broker(tmp_path, h)
    q = b.snapshot(["1155"], market=MY)["1155"]
    assert seen["codes"] == ["MY.1155"] and str(q["ts"].tzinfo) == "Asia/Kuala_Lumpur"
    assert b._code("D05", SG) == "SG.D05" and b._code("SPY") == "US.SPY" and b._code("US.AAPL", MY) == "US.AAPL"


# ---- choosing which markets run ------------------------------------------------------------------------------------
class SyncRows:
    def __init__(self, rows): self.rows = rows
    def get_market_configs(self): return self.rows


class DummyRunner:
    enabled = True
    def __init__(self): self.stopped = False
    def run_forever(self, stop): stop.wait(0.05)


def test_supervisor_starts_only_enabled_markets_and_applies_the_switch(tmp_path):
    from bot.intraday.supervisor import MarketSupervisor
    made, logs = [], []

    def make(code, row, universe):
        made.append((code, row.get("paper_balance"), universe()))
        return DummyRunner()

    rows = [{"market": "US", "enabled": True, "symbols": ["SPY", " qqq "], "paper_balance": 0},
            {"market": "MY", "enabled": False, "symbols": ["1155"], "paper_balance": 50000},
            {"market": "SG", "enabled": True, "symbols": [], "paper_balance": 20000}]
    sup = MarketSupervisor(SyncRows(rows), make, log=logs.append, markets_path=str(tmp_path / "none.json"))
    sup.poll_once()
    assert sorted(sup.runners) == ["SG", "US"] and made[0][2] == ["SPY", "QQQ"]
    assert sup.symbols_for("SG") == ["ES3"]                      # empty list falls back to the market's sample symbol
    rows[1]["enabled"] = True                                    # user switches Malaysia on in the app
    rows[0]["enabled"] = False                                   # ...and the US off
    sup.poll_once()
    assert sorted(sup.runners) == ["MY", "SG", "US"]
    assert sup.runners["US"].enabled is False and sup.runners["MY"].enabled is True
    assert any("switched off" in m for m in logs) and any("MY engine started" in m for m in logs)
    sup.stop.set()


def test_supervisor_reports_a_market_that_cannot_start_and_hints_when_nothing_is_on(tmp_path):
    from bot.intraday.supervisor import MarketSupervisor
    logs = []

    def boom(code, row, universe):
        raise RuntimeError("no data feed")

    sup = MarketSupervisor(SyncRows([{"market": "MY", "enabled": True, "symbols": [], "paper_balance": 1}]), boom, log=logs.append,
                           markets_path=str(tmp_path / "none.json"))
    sup.poll_once()
    assert sup.runners == {} and any("cannot start MY: no data feed" in m for m in logs)
    quiet = MarketSupervisor(SyncRows([]), boom, log=logs.append, markets_path=str(tmp_path / "none.json"))
    quiet.poll_once(); quiet.poll_once()
    assert sum("no market is switched on" in m for m in logs) == 1


def test_observed_sessions_detect_the_lunch_break_and_mismatches():
    from bot.intraday.market import configured_runs, observed_sessions
    bars = synthetic_day(D, "range", 1, 10.0, MY)
    assert observed_sessions(bars, MY) == configured_runs(MY) == [("09:00", "12:29"), ("14:30", "16:59")]
    assert observed_sessions(synthetic_day(D, "range", 1, 10.0, SG), SG) == [("09:00", "11:59"), ("13:00", "16:59")]
    assert observed_sessions([], MY) == []
    continuous = [Bar(datetime(2026, 9, 2, 9, 0, tzinfo=MY.tz) + timedelta(minutes=i), 1, 1, 1, 1, 1) for i in range(480)]
    assert observed_sessions(continuous, MY) == [("09:00", "16:59")] != configured_runs(MY)   # would be flagged by the smoke test


# ---- the smoke script itself (it shipped broken once because nothing ran it) ----------------------------------------
class StubBroker:
    acc_id = "1"
    _pos_market = 100

    def __init__(self, fail_my=None, fail_sg=None):
        self.fail = {"MY": fail_my, "SG": fail_sg}

    def _call(self, method, path, params=None, **kw):
        """Raw history-kline: one page ending on `end` (date string), like the real endpoint for a short window."""
        day = date.fromisoformat(params["end"])
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        rows = [{"time_key": int(b.ts.timestamp() * 1000), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                 "volume": b.volume} for b in synthetic_day(day, "range", 1, 100.0, US)]
        return {"kline_list": rows[:370], "next_time": None}

    def basic_info(self, codes):
        return [{"code": c, "name": "Maybank", "lot_size": 100, "exchange": "BMS", "state": "NORMAL"} for c in codes if c == "MY.1155"]

    def cash(self): return 1000.0
    def positions(self): return []
    def last_price(self, s): return 100.0
    def history(self, s, n): return [1.0] * n

    def snapshot(self, symbols, market=US):
        if self.fail.get(market.code):
            raise RuntimeError(self.fail[market.code])
        return {symbols[0]: {"last": 5.0, "ts": datetime.now(market.tz), "bid": 0, "ask": 0}}

    def intraday_bars(self, symbol, start, end, market=US, **kw):
        if self.fail.get(market.code):
            raise RuntimeError(self.fail[market.code])
        days = business_days(max(start, end - timedelta(days=4)), end)
        return [b for d in days for b in synthetic_day(d, "range", 1, 10.0, market)] if days else []


def test_smoke_checks_run_end_to_end_and_explain_refusals():
    from bot.smoke_moomoo import run_checks
    lines = []
    run_checks(StubBroker(), out=lines.append)
    text = "\n".join(lines)
    assert "[FAIL]" not in text and "matches the configured hours" in text and "regular" in text and "age=" in text
    assert "30-day request, page 1: 370 bars" in text and "single day" in text and "regular-session" in text
    assert "MY.1155: Maybank | board lot 100 | exchange BMS" in text and "SG.D05: NOT RECOGNISED" in text
    lines.clear()
    run_checks(StubBroker(fail_sg="realtime quote permission required", fail_my="unsupported market"), out=lines.append)
    text = "\n".join(lines)
    assert "no real-time Singapore (SGX) quote right" in text and "does not serve Malaysia (Bursa) price data" in text
    assert "--csv-dir data/my" in text and "[FAIL]" not in text      # a refusal is a finding, not a crash


def test_explain_error_passes_other_errors_through():
    from bot.intraday.market import explain_error
    assert explain_error(RuntimeError("boom"), MY) == "boom"
    assert "quote right" in explain_error(RuntimeError("Realtime quote permission required"), SG)


def test_live_engine_reports_a_data_problem_once_and_recovers(tmp_path):
    from test_live import Clock, FakeBroker, FakeSync
    from test_intraday import Fixed
    from bot.intraday.live import LiveRunner
    clock, logs = Clock(), []
    broker = FakeBroker({"1155": _my_flat_day(D - timedelta(days=1)) + _my_flat_day(D)}, clock)
    real = broker.intraday_bars
    broker.intraday_bars = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unsupported market"))
    sync = FakeSync()
    r = LiveRunner(broker, sync, universe=["1155"], configs=lambda: [{"strategy": "A", "enabled": True, "budget": 1_000_000, "params": {}}],
                   calendar_path=str(tmp_path / "c.json"), state_path=str(tmp_path / "s.json"), journal_path=str(tmp_path / "j.jsonl"),
                   clock=lambda: clock.now, sleep=lambda s: None, log=logs.append, fill_timeout=0.01, market=MY,
                   strategy_factory=lambda c, o: [Fixed([], name="A")])
    clock.now = MY.ts_at(D, 30) + timedelta(minutes=1, seconds=3)
    for _ in range(5):
        r.cycle()                                   # repeated cycles must not spam or crash
    assert sum("data problem" in m for m in logs) == 1
    assert "does not serve Malaysia" in sync.status[-1]["data_error"]
    broker.intraday_bars = real
    clock.now += timedelta(seconds=90)              # past the 60 s back-off
    r.cycle()
    assert r.data_error is None and r.day == D and sync.status[-1]["data_error"] is None


def test_invalid_symbol_gets_actionable_advice():
    from bot.intraday.market import explain_error
    msg = explain_error(RuntimeError("invalid symbol"), MY)
    assert "does not recognise" in msg and "--probe MY." in msg


def test_basic_info_request(tmp_path):
    from test_moomoo_rest import broker, ok_sim
    seen = {}

    def h(req):
        seen["body"] = __import__("json").loads(req.content)
        return ok_sim({"basic_list": [{"code": "MY.1155", "lot_size": 100}]})

    assert broker(tmp_path, h).basic_info(["MY.1155", "MY.NOPE"]) == [{"code": "MY.1155", "lot_size": 100}]
    assert seen["body"] == {"code_list": ["MY.1155", "MY.NOPE"]}
