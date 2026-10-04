from datetime import date, datetime, time, timedelta

import pytest

from bot.intraday.backtest import Backtester
from bot.intraday.bars import NY, Bar
from bot.intraday.calendar import Calendar
from bot.intraday.context import DayContext, TickerState
from bot.intraday.data import business_days, synthetic_history
from bot.intraday.params import ScalpParams, SharedParams, apply_overrides
from bot.intraday.risk import DayRisk, size_position
from bot.intraday.stats import summarize
from bot.intraday.strategies import Range, Scalp, Signal, Strategy, Trend

D1, D2 = date(2026, 9, 1), date(2026, 9, 2)  # D1 = warm-up day, D2 = trading day


def mkday(day, overrides=None):
    """390 flat 1m bars at 100; overrides = {minute_index: (open, high, low, close)}."""
    overrides = overrides or {}
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    out = []
    for m in range(390):
        o, h, l, c = overrides.get(m, (100, 100.05, 99.95, 100))
        out.append(Bar(start + timedelta(minutes=m), o, h, l, c, 1000))
    return out


class Fixed(Strategy):
    """Emits a long signal at the close of given 1m bar indices: entry = close, stop = entry-1, T1 = entry+1.5."""

    def __init__(self, at, name="X", family="trend", t2=None, time_stop=None, always=False, flat=time(15, 55)):
        self.at, self.name, self.family, self.t2 = set(at), name, family, t2
        self.time_stop, self.always, self.flat = time_stop, always, flat

    def on_bar(self, ctx, new5):
        if len(ctx.bars1) - 1 in self.at:
            e = ctx.bars1[-1].close
            return Signal(self.name, ctx.ticker, ctx.now, e, e - 1.0, e + 1.5, self.t2 and e + self.t2, {},
                          time_stop_minutes=self.time_stop, time_stop_always=self.always, flat=self.flat,
                          family=self.family)


def run(strats, over=None, shared=None, cal=None, budget=100000):
    data = {"SPY": mkday(D1) + mkday(D2, over)}
    return Backtester(strats, shared or SharedParams(), budget, cal).run(data)


# ---- sizing & params ----------------------------------------------------------------------------------------
def test_sizing_formula_and_binding_cap():
    p = SharedParams()
    s = size_position(100000, 100.0, 99.0, p)  # by risk 500, by notional 1000
    assert s["shares"] == 500 and s["binding"] == "risk" and s["skip"] is None
    s = size_position(100000, 100.0, 99.8, p)  # by risk 2500, by notional 1000 -> notional binds
    assert s["shares"] == 1000 and s["binding"] == "notional"


def test_sizing_skips():
    p = SharedParams()
    assert size_position(100, 100.0, 99.0, p)["skip"] == "shares < 1"
    assert size_position(100000, 100.0, 100.0, p)["skip"]
    # cost 0.02/share vs 10% of a $0.10 risk-per-share (=0.01) -> skip
    assert "cost" in size_position(100000, 100.0, 99.9, p)["skip"]


def test_params_validation():
    with pytest.raises(ValueError, match="exceed 1.0"):
        SharedParams(risk_per_trade_pct=1.5).validate()
    with pytest.raises(ValueError, match="long only"):
        SharedParams(allow_short=True).validate()
    with pytest.raises(ValueError, match="unknown parameter"):
        apply_overrides(ScalpParams(), {"nope": 1})
    p = apply_overrides(ScalpParams(), {"window_start": "10:00", "t1_r": "2", "pullback_max_age_bars": 5})
    assert p.window_start == time(10, 0) and p.t1_r == 2.0 and p.pullback_max_age_bars == 5


def test_day_risk_limits():
    p = SharedParams()
    r = DayRisk(100000)
    assert r.can_enter(p) is None
    r.record(-100); r.record(-100)
    assert "consecutive" in r.can_enter(p)
    r2 = DayRisk(100000); r2.record(-1500)
    assert "daily loss" in r2.can_enter(p)
    r3 = DayRisk(100000); r3.trades = 3
    assert "max trades" in r3.can_enter(p)


# ---- context: aggregation, levels, no lookahead --------------------------------------------------------------
def test_5m_bars_close_only_when_complete_and_levels_form():
    ctx = DayContext("SPY", D2, TickerState())
    bars = mkday(D2, {3: (100, 101, 99.5, 100)})
    closed_at = []
    for i, b in enumerate(bars[:59]):  # up to 10:28; 10:29 is the last IB minute
        if ctx.add_1m(b):
            closed_at.append(i)
    assert closed_at[:3] == [4, 9, 14]  # 5th, 10th, 15th minute
    assert ctx.bars5[0].ts.minute == 30 and ctx.bars5[0].high == 101 and ctx.bars5[0].low == 99.5
    assert ctx.or_ready and ctx.or_high == 101 and ctx.or_low == 99.5
    assert ctx.ib_ready is False
    ctx.add_1m(bars[59])
    assert ctx.ib_ready and ctx.ib_high == 101


def test_vwap_cross_counting_and_rising():
    ctx = DayContext("SPY", D2, TickerState())
    t0 = datetime(2026, 9, 2, 9, 30, tzinfo=NY)
    ctx.side = [(t0 + timedelta(minutes=5 * i), s) for i, s in enumerate([1, 1, -1, 1, -1])]
    assert ctx.vwap_crosses() == 3
    assert ctx.vwap_crosses(time(9, 45)) == 1  # only the last two (09:45 and 09:50 bars) -> one flip
    ctx.vwap5 = [1, 2, 3, 4]
    assert ctx.vwap_rising()
    ctx.vwap5 = [4, 3, 2, 1]
    assert not ctx.vwap_rising()


def test_indicators_carry_across_days_but_vwap_resets():
    st = TickerState()
    for day, px in ((D1, 100), (D2, 120)):
        ctx = DayContext("SPY", day, st)
        for b in mkday(day):
            ctx.add_1m(Bar(b.ts, px, px + .05, px - .05, px, 1000))
        assert abs(ctx.vwap - px) < 1e-6  # VWAP only reflects today
    assert st.ema20.value is not None and 100 < st.ema20.value < 120 + 1e-9  # carried over, but caught up


def test_no_lookahead_truncating_the_future_does_not_change_earlier_trades():
    days = business_days(date(2026, 9, 1), date(2026, 9, 12))
    data = synthetic_history(days, ["trend", "range"], ["SPY", "QQQ"])
    cutoff = datetime(days[-1].year, days[-1].month, days[-1].day, 12, 0, tzinfo=NY)

    def go(d):
        return Backtester([Scalp(), Trend(), Range()], SharedParams(), 100000).run(d).trades

    full = go(data)
    cut = go({t: [b for b in bars if b.ts < cutoff] for t, bars in data.items()})
    done = lambda tr: [(t["entry_ts"], t["ticker"], t["strategy"], t["pnl"]) for t in tr  # noqa: E731
                       if all(e["reason"] != "end_of_data" for e in t["exits"]) and t["exits"][-1]["ts"] < cutoff.isoformat()]
    assert done(cut) and done(cut) == done(full)[: len(done(cut))]


# ---- fills & exits --------------------------------------------------------------------------------------------
def test_stop_loss():
    r = run([Fixed([40])], {45: (100, 100.1, 98.9, 99.2)})
    t = r.trades[0]
    assert t["shares"] == 500 and [e["reason"] for e in t["exits"]] == ["stop"] and t["exits"][0]["price"] == 99.0
    assert t["pnl"] == pytest.approx(-500 - 10) and t["r"] == pytest.approx(-1.02)


def test_stop_assumed_first_when_bar_touches_stop_and_target():
    r = run([Fixed([40])], {45: (100, 101.6, 98.9, 100)})
    assert [e["reason"] for e in r.trades[0]["exits"]] == ["stop"]


def test_target1_partial_then_breakeven_stop():
    r = run([Fixed([40])], {45: (100, 101.6, 99.9, 101.5), 46: (101.4, 101.5, 99.9, 100.2)})
    t = r.trades[0]
    assert [(e["reason"], e["qty"], e["price"]) for e in t["exits"]] == [("target1", 250, 101.5), ("breakeven_stop", 250, 100)]
    assert t["pnl"] == pytest.approx(250 * 1.5 - 10)


HOLD = {i: (101.2, 101.3, 101.1, 101.2) for i in range(46, 390)}  # stays above the breakeven stop


def test_target2_after_target1():
    r = run([Fixed([40], t2=2.5)], {**HOLD, 45: (100, 101.6, 99.9, 101.5), 47: (101.5, 102.6, 101.4, 102.5)})
    assert [e["reason"] for e in r.trades[0]["exits"]] == ["target1", "target2"]


def test_gap_through_stop_fills_at_open():
    r = run([Fixed([40])], {45: (98.5, 98.7, 98.4, 98.6)})
    t = r.trades[0]
    assert t["exits"][0]["reason"] == "stop_gap" and t["exits"][0]["price"] == 98.5
    assert t["pnl"] == pytest.approx(-1.5 * 500 - 10)


def test_entry_not_filled_when_price_runs_away():
    r = run([Fixed([40])], {41: (101, 101.2, 100.8, 101)})
    assert r.trades == []


def test_entry_fills_at_open_when_it_gaps_in_our_favour():
    r = run([Fixed([40])], {41: (99.5, 99.8, 99.4, 99.6), 45: (99.5, 99.6, 98.0, 98.2)})
    assert r.trades[0]["entry"] == 99.5


def test_single_share_position_exits_fully_at_target1():
    r = run([Fixed([40])], {45: (100, 101.6, 99.9, 101.5)}, budget=300)  # 300*0.5%/1 = 1.5 -> 1 share
    t = r.trades[0]
    assert t["shares"] == 1 and [e["reason"] for e in t["exits"]] == ["target1"]


def test_time_stop_only_until_target1_for_scalp_style():
    r = run([Fixed([40], time_stop=25)])  # flat price: never hits anything
    e = r.trades[0]["exits"][0]
    assert e["reason"] == "time_stop"
    entry = datetime(2026, 9, 2, 10, 11, tzinfo=NY)  # signal at bar 40 (10:10), fills on bar 41
    assert datetime.fromisoformat(e["ts"]) - entry == timedelta(minutes=25)


def test_time_stop_always_applies_even_after_target1():
    r = run([Fixed([40], time_stop=90, always=True)], {**HOLD, 45: (100, 101.6, 99.9, 101.5)})
    assert [e["reason"] for e in r.trades[0]["exits"]] == ["target1", "time_stop"]


def test_flat_at_end_of_day():
    r = run([Fixed([350])])
    e = r.trades[0]["exits"][0]
    assert e["reason"] == "flat_eod" and datetime.fromisoformat(e["ts"]).time() == time(15, 55)


# ---- shared risk layer & calendar ------------------------------------------------------------------------------
STOPS = {i: (100, 100.1, 98.9, 99.2) for i in (43, 83, 123, 163)}


def test_stops_trading_after_two_consecutive_losses():
    r = run([Fixed([40, 80, 120, 160])], STOPS)
    assert len(r.trades) == 2


def test_daily_loss_limit_halts_new_entries():
    sh = SharedParams(risk_per_trade_pct=1.0, stop_after_consecutive_losses=9)
    r = run([Fixed([40, 80, 120, 160])], STOPS, shared=sh)
    assert len(r.trades) == 2  # -1.01% twice => beyond the 1.5% limit


def test_max_trades_per_day():
    sh = SharedParams(max_trades_per_day=1, stop_after_consecutive_losses=9)
    assert len(run([Fixed([40, 80, 120, 160])], STOPS, shared=sh).trades) == 1


def test_one_position_at_a_time():
    r = run([Fixed([40, 41, 42])])  # signals while the first position is still open
    assert len(r.trades) == 1


def test_fomc_and_half_days_are_not_traded():
    assert run([Fixed([40])], cal=Calendar(fomc_days={D2}, configured=True)).trades == []
    assert run([Fixed([40])], cal=Calendar(half_days={D2}, configured=True)).trades == []


def test_cpi_nfp_day_blocks_entries_before_10():
    cal = Calendar(cpi_nfp_days={D2}, configured=True)
    assert run([Fixed([20])], cal=cal).trades == []      # signal at 09:50
    assert len(run([Fixed([40])], cal=cal).trades) == 1  # signal at 10:10


def test_missing_calendar_is_flagged_in_notes():
    assert any("no event calendar" in n for n in run([Fixed([40])]).notes)
    assert not any("no event calendar" in n for n in run([Fixed([40])], cal=Calendar(configured=True)).notes)


def test_trend_and_range_do_not_run_on_the_same_ticker_same_day():
    over = {45: (100, 101.6, 99.9, 101.5), 46: (101.5, 101.6, 100.9, 101.0)}
    r = run([Fixed([40], "A", "trend"), Fixed([200], "C", "range")], over)
    assert [t["strategy"] for t in r.trades] == ["A"]


# ---- strategies on synthetic data & stats ------------------------------------------------------------------------
def test_each_strategy_trades_on_its_matching_synthetic_regime_and_journals_fully():
    days = business_days(date(2026, 9, 1), date(2026, 9, 15))
    trend = synthetic_history(days, ["trend"], ["SPY", "QQQ"])
    rng = synthetic_history(days, ["range"], ["SPY", "QQQ"])
    for S, data in ((Scalp, trend), (Trend, trend), (Range, rng)):
        res = Backtester([S()], SharedParams(), 100000).run(data)
        assert res.trades, S.__name__
        for t in res.trades:
            assert t["entry"] > t["stop"] and sum(e["qty"] for e in t["exits"]) == t["shares"]
            assert t["regime"]["sizing"]["shares"] == t["shares"] and "r" in t and t["costs"] > 0
    assert Backtester([Range()], SharedParams(), 100000).run(trend).trades == []  # C is silent on trend days


def test_summarize():
    trades = [{"pnl": 100, "r": 1.0, "date": "d1", "minutes_held": 10, "costs": 1},
              {"pnl": -50, "r": -0.5, "date": "d1", "minutes_held": 20, "costs": 1},
              {"pnl": 150, "r": 1.5, "date": "d2", "minutes_held": 30, "costs": 1}]
    s = summarize(trades, 10000)
    assert s["n_trades"] == 3 and s["win_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert s["total_pnl"] == 200 and s["profit_factor"] == 5.0 and s["days_traded"] == 2
    assert s["max_drawdown_pct"] == pytest.approx(50 / 10100 * 100, abs=0.01)
    assert summarize([], 10000)["note"] == "no trades"
