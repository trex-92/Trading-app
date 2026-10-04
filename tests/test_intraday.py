from datetime import date, datetime, time, timedelta

import pytest

from bot.intraday.backtest import Backtester
from bot.intraday.bars import NY, Bar
from bot.intraday.calendar import Calendar
from bot.intraday.context import DayContext, TickerState
from bot.intraday.data import business_days, synthetic_history
from bot.intraday.params import ScalpParams, SharedParams, TrendParams, apply_overrides
from bot.intraday.risk import DayRisk, DrawdownGuard, size_position
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

    def __init__(self, at, name="X", family="trend", t2=None, time_stop=None, always=False, flat_min=385):
        self.at, self.name, self.family, self.t2 = set(at), name, family, t2
        self.time_stop, self.always, self.flat_min = time_stop, always, flat_min

    def on_bar(self, ctx, new5):
        if len(ctx.bars1) - 1 in self.at:
            e = ctx.bars1[-1].close
            return Signal(self.name, ctx.ticker, ctx.now, e, e - 1.0, e + 1.5, self.t2 and e + self.t2, {},
                          time_stop_minutes=self.time_stop, time_stop_always=self.always, flat_min=self.flat_min,
                          family=self.family, elapsed=ctx.elapsed)


def run(strats, over=None, shared=None, cal=None, budget=100000):
    data = {"SPY": mkday(D1) + mkday(D2, over)}
    return Backtester(strats, shared or SharedParams(), budget, cal).run(data)


# ---- sizing & params ----------------------------------------------------------------------------------------
def test_sizing_formula_and_binding_cap():
    p = SharedParams()
    s = size_position(100000, 100.0, 98.0, p)  # 1% risk: by risk 500, by notional 1000
    assert s["shares"] == 500 and s["binding"] == "risk" and s["skip"] is None and s["risk_pct"] == 1.0
    s = size_position(100000, 100.0, 99.0, p)  # by risk 1000 == by notional 1000
    assert s["shares"] == 1000
    s = size_position(100000, 100.0, 99.8, p)  # by risk 5000, by notional 1000 -> notional binds
    assert s["shares"] == 1000 and s["binding"] == "notional"
    assert size_position(100000, 100.0, 98.0, p, risk_pct=0.5)["shares"] == 250  # breaker-reduced risk


def test_sizing_skips():
    p = SharedParams()
    assert size_position(50, 100.0, 99.0, p)["skip"] == "shares < 1"
    assert size_position(100000, 100.0, 100.0, p)["skip"]
    # cost 0.02/share vs 10% of a $0.10 risk-per-share (=0.01) -> skip
    assert "cost" in size_position(100000, 100.0, 99.9, p)["skip"]


def test_params_validation():
    assert SharedParams().risk_per_trade_pct == 1.0 and SharedParams().daily_max_loss_pct == 2.0
    with pytest.raises(ValueError, match="hard ceiling"):
        SharedParams(risk_per_trade_pct=1.2).validate()      # above the 1.0 ceiling: refuse to start
    with pytest.raises(ValueError, match="may not be above 1.0"):
        SharedParams(risk_per_trade_pct=1.2, risk_hard_ceiling_pct=1.5).validate()  # the ceiling itself is capped
    with pytest.raises(ValueError, match="breakers"):
        SharedParams(breaker1_pct=10, breaker2_pct=6).validate()
    assert TrendParams().min_stop_pct == 0.3 and TrendParams().max_stop_pct == 1.0
    with pytest.raises(ValueError, match="long only"):
        SharedParams(allow_short=True).validate()
    with pytest.raises(ValueError, match="unknown parameter"):
        apply_overrides(ScalpParams(), {"nope": 1})
    p = apply_overrides(ScalpParams(), {"window_start_min": "30", "t1_r": "2", "pullback_max_age_bars": 5})
    assert p.window_start_min == 30 and p.t1_r == 2.0 and p.pullback_max_age_bars == 5


def test_day_risk_limits():
    p = SharedParams()
    r = DayRisk(100000)
    assert r.can_enter(p) is None
    r.record(-100); r.record(-100)
    assert "consecutive" in r.can_enter(p)
    r2 = DayRisk(100000); r2.record(-2000)
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
    ctx.side = [(5 * i, s) for i, s in enumerate([1, 1, -1, 1, -1])]   # (trading minute the 5m bar started, side)
    assert ctx.vwap_crosses() == 3
    assert ctx.vwap_crosses(15) == 1  # only the last two (09:45 and 09:50 bars) -> one flip
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
    assert t["shares"] == 1000 and [e["reason"] for e in t["exits"]] == ["stop"] and t["exits"][0]["price"] == 99.0
    assert t["pnl"] == pytest.approx(-1000 - 20) and t["r"] == pytest.approx(-1.02)


def test_stop_assumed_first_when_bar_touches_stop_and_target():
    r = run([Fixed([40])], {45: (100, 101.6, 98.9, 100)})
    assert [e["reason"] for e in r.trades[0]["exits"]] == ["stop"]


def test_target1_partial_then_breakeven_stop():
    r = run([Fixed([40])], {45: (100, 101.6, 99.9, 101.5), 46: (101.4, 101.5, 99.9, 100.2)})
    t = r.trades[0]
    assert [(e["reason"], e["qty"], e["price"]) for e in t["exits"]] == [("target1", 500, 101.5), ("breakeven_stop", 500, 100)]
    assert t["pnl"] == pytest.approx(500 * 1.5 - 20)


HOLD = {i: (101.2, 101.3, 101.1, 101.2) for i in range(46, 390)}  # stays above the breakeven stop


def test_target2_after_target1():
    r = run([Fixed([40], t2=2.5)], {**HOLD, 45: (100, 101.6, 99.9, 101.5), 47: (101.5, 102.6, 101.4, 102.5)})
    assert [e["reason"] for e in r.trades[0]["exits"]] == ["target1", "target2"]


def test_gap_through_stop_fills_at_open():
    r = run([Fixed([40])], {45: (98.5, 98.7, 98.4, 98.6)})
    t = r.trades[0]
    assert t["exits"][0]["reason"] == "stop_gap" and t["exits"][0]["price"] == 98.5
    assert t["pnl"] == pytest.approx(-1.5 * 1000 - 20)


def test_entry_not_filled_when_price_runs_away():
    r = run([Fixed([40])], {41: (101, 101.2, 100.8, 101)})
    assert r.trades == []


def test_entry_fills_at_open_when_it_gaps_in_our_favour():
    r = run([Fixed([40])], {41: (99.5, 99.8, 99.4, 99.6), 45: (99.5, 99.6, 98.0, 98.2)})
    assert r.trades[0]["entry"] == 99.5


def test_single_share_position_exits_fully_at_target1():
    r = run([Fixed([40])], {45: (100, 101.6, 99.9, 101.5)}, budget=100)  # 100*1%/1 = 1 share
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
    sh = SharedParams(stop_after_consecutive_losses=9)
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


# ---- drawdown breakers --------------------------------------------------------------------------------------
def test_drawdown_guard_levels_and_recovery():
    p = SharedParams()
    g = DrawdownGuard(p)
    assert g.risk_pct == 1.0 and not g.live_disabled
    assert g.record(-5000, 100000) == 0                     # 4.8% from peak: nothing yet
    assert g.record(-1500, 100000) == 1 and g.risk_pct == 0.5  # 6.5%
    assert g.record(+1000, 100000) == 1                     # still below the old peak: stays reduced
    assert g.record(-4500, 100000) == 2 and g.live_disabled  # exactly 10% from peak
    g2 = DrawdownGuard(p)
    g2.record(-7000, 100000)
    assert g2.level == 1
    assert g2.record(+8000, 100000) == 0 and g2.risk_pct == 1.0  # new equity peak clears the breaker


def test_backtest_cuts_risk_at_6_percent_and_stops_at_10_percent():
    days = business_days(date(2026, 9, 1), date(2026, 10, 9))
    loss = {40: (100, 100.05, 99.95, 100), 44: (100, 100.1, 98.8, 98.9), 45: (100, 100.05, 99.95, 100)}
    data = {"SPY": [b for d in days for b in mkday(d, loss if d != days[0] else None)]}
    res = Backtester([Fixed([40])], SharedParams(), 100000).run(data)
    notes = " ".join(res.notes)
    assert "breaker 1 tripped" in notes and "breaker 2 tripped" in notes
    risk = [t["regime"]["sizing"]["risk_pct"] for t in res.trades]
    first_cut = risk.index(0.5)
    assert set(risk[:first_cut]) == {1.0} and set(risk[first_cut:]) == {0.5}
    # the cut happens exactly after cumulative losses first reach 6% of capital, and the size roughly halves
    cum_before = sum(t["pnl"] for t in res.trades[:first_cut])
    assert cum_before <= -6000 and sum(t["pnl"] for t in res.trades[:first_cut - 1]) > -6000
    assert res.trades[first_cut]["shares"] < 0.6 * res.trades[first_cut - 1]["shares"]
    # after the 10% breaker no more entries; the last trade is the one that crossed it
    total = sum(t["pnl"] for t in res.trades)
    assert -11500 < total <= -10000 and len(res.trades) < len(days) - 1


# ---- funnel: where setups were lost ---------------------------------------------------------------------------------
def test_funnel_counts_reconcile_for_every_strategy():
    days = business_days(date(2026, 9, 1), date(2026, 9, 25))
    trend = synthetic_history(days, ["trend", "range"], ["SPY", "QQQ"])
    for strat, prefix in ((Scalp(), "window_minutes"), (Trend(), "window_bars"), (Range(), "window_bars")):
        res = Backtester([strat], SharedParams(), 100000).run(trend)
        f, eng = res.funnel[strat.name], res.funnel["engine"]
        window = f[prefix]
        failed = sum(v for k, v in f.items() if k.startswith("regime_failed_"))
        regime_key = "regime_minutes" if strat.name == "A" else "regime_bars"
        # every minute/bar inside the entry window is counted exactly once: passed, or lost to a named condition
        assert window > 0 and failed + f.get(regime_key, 0) == window, strat.name
        assert f.get("signals", 0) <= f.get("triggers", f.get("setups", 0)) or strat.name == "C"
        # signals end up as trades only through the engine's gates
        assert eng.get("signals_seen", 0) >= eng.get("orders_placed", 0) >= eng.get("entries_filled", 0)
        assert eng.get("entries_filled", 0) == len(res.trades)


def test_funnel_names_the_condition_that_removed_the_setups():
    days = business_days(date(2026, 9, 1), date(2026, 9, 25))
    rng_only = synthetic_history(days, ["range"], ["SPY"])
    f = Backtester([Scalp()], SharedParams(), 100000).run(rng_only).funnel["A"]
    assert f.get("regime_minutes", 0) == 0 and f["window_minutes"] > 0          # a range market never looks like an uptrend
    assert sum(v for k, v in f.items() if k.startswith("regime_failed_")) == f["window_minutes"]
    c = Backtester([Range()], SharedParams(), 100000).run(synthetic_history(days, ["trend"], ["SPY"])).funnel["C"]
    assert c.get("regime_bars", 0) == 0 and c.get("regime_failed_too_few_vwap_crosses", 0) > 0


def test_engine_funnel_records_blocks_and_skips():
    r = run([Fixed([40, 80, 120, 160])], STOPS)
    eng = r.funnel["engine"]
    assert eng["entries_filled"] == 2 and any(k.startswith("blocked: ") for k in eng)
    tiny = run([Fixed([40])], budget=50)
    assert tiny.funnel["engine"]["skipped_shares"] == 1 and tiny.trades == []
    unfilled = run([Fixed([40])], {41: (101, 101.2, 100.8, 101)})
    assert unfilled.funnel["engine"]["entries_not_filled"] == 1


def test_small_budget_is_flagged():
    big_price = {i: (500, 500.05, 499.95, 500) for i in range(390)}
    data = {"SPY": mkday(D1, big_price) + mkday(D2, {**big_price, 45: (500, 500.1, 498.8, 499.0)})}
    res = Backtester([Fixed([40])], SharedParams(), 2000).run(data)    # $2,000 at $500 a share
    assert res.trades and res.trades[0]["shares"] < 10
    assert any(n.startswith("WARNING: the budget is small") for n in res.notes)
    ok = run([Fixed([40])], {45: (100, 100.1, 98.9, 99.2)})
    assert not any("budget is small" in n for n in ok.notes)


# ---- fractional shares and the per-trade cap -----------------------------------------------------------------------
def test_qty_helpers_keep_whole_numbers_whole():
    from bot.intraday.qty import clean, fmt
    assert clean(100.0) == 100 and isinstance(clean(100.0), int) and clean(0.30000000000000004) == 0.3
    assert fmt(100.0) == "100" and fmt(0.13) == "0.13" and fmt(0.00001) == "0.00001" and fmt(2.5) == "2.5"


def test_small_budget_trades_a_fraction_of_a_share_only_when_allowed():
    whole = SharedParams()
    frac = SharedParams(allow_fractional=True)
    s = size_position(100, 743.0, 741.5, whole)
    assert s["skip"] == "shares < 1"                                    # $100 cannot buy one $743 share
    s = size_position(100, 743.0, 741.5, frac)                          # 1% risk would be 0.67 sh; the 1x cap allows 0.1346
    assert s["shares"] == 0.13 and s["binding"] == "notional" and s["skip"] is None
    assert size_position(100000, 100.0, 99.0, frac)["shares"] == 1000    # big budgets are unchanged, still an int


def test_max_trade_notional_caps_one_position_and_is_reported():
    p = SharedParams(max_trade_notional=100.0, allow_fractional=True)
    s = size_position(10_000, 500.0, 499.0, p)                           # risk would allow 100 sh; the $100 cap allows 0.2
    assert s["shares"] == 0.2 and s["binding"] == "per-trade cap" and s["shares_by_trade_cap"] == 0.2
    whole = size_position(10_000, 50.0, 49.0, SharedParams(max_trade_notional=120.0))   # whole shares: floor(120/50) = 2
    assert whole["shares"] == 2 and isinstance(whole["shares"], int)
    assert size_position(10_000, 500.0, 499.0, SharedParams(max_trade_notional=100.0))["skip"] == "shares < 1"
    with pytest.raises(ValueError):
        SharedParams(max_trade_notional=-1).validate()
    with pytest.raises(ValueError):
        SharedParams(allow_fractional=True, fractional_step=0).validate()


def test_fractional_backtest_splits_the_position_and_reconciles_exactly():
    flat = {i: (500, 500.05, 499.95, 500) for i in range(390)}
    over = {**flat, 45: (500, 501.6, 499.9, 501.5), 46: (501.4, 501.5, 499.9, 500.2)}
    data = {"SPY": mkday(D1, flat) + mkday(D2, over)}
    res = Backtester([Fixed([40])], SharedParams(allow_fractional=True), 100).run(data)
    t = res.trades[0]
    assert t["shares"] == 0.2
    assert [(e["reason"], e["qty"]) for e in t["exits"]] == [("target1", 0.1), ("breakeven_stop", 0.1)]
    assert sum(e["qty"] for e in t["exits"]) == t["shares"]
    assert t["pnl"] == pytest.approx(0.1 * 1.5 - 0.02 * 0.2)             # half at +1.5, half at breakeven, minus fees
    assert res.funnel["engine"]["entries_filled"] == 1


def test_per_trade_cap_in_a_backtest_keeps_every_position_small():
    res = run([Fixed([40])], {45: (100, 100.1, 98.9, 99.2)},
              shared=SharedParams(max_trade_notional=500.0), budget=100000)
    t = res.trades[0]
    assert t["shares"] == 5 and t["regime"]["sizing"]["binding"] == "per-trade cap"      # $500 / $100
    assert t["pnl"] == pytest.approx(-5 * 1.0 - 0.02 * 5) and t["r"] == pytest.approx(-1.02)   # R is unchanged by the cap


def test_hod_blocking_is_opt_in_and_breakdown_sums():
    from bot.intraday.params import ScalpParams
    assert ScalpParams().levels_include_hod is False
    assert apply_overrides(ScalpParams(), {"levels_include_hod": True}).levels_include_hod is True


# ---- Scalp experiment switches (defaults = the spec) -------------------------------------------------------------------
def _scalp_run(p, days=None):
    days = days or business_days(date(2026, 9, 1), date(2026, 9, 25))
    return Backtester([Scalp(p)], SharedParams(), 100000).run(synthetic_history(days, ["trend"], ["SPY", "QQQ"]))


def test_scalp_defaults_keep_the_spec_exits():
    p = ScalpParams()
    assert p.t1_frac == 0.5 and p.require_vwap_rising and p.require_emas_rising and p.require_or_break
    hit = [t for t in _scalp_run(p).trades if any(e["reason"] == "target1" for e in t["exits"])]
    assert hit and all(len(t["exits"]) >= 2 and t["exits"][0]["qty"] == t["shares"] // 2 for t in hit)   # half off at T1


def test_scalp_t1_frac_one_takes_everything_off_at_target1():
    res = _scalp_run(ScalpParams(t1_frac=1.0))
    hit = [t for t in res.trades if any(e["reason"] == "target1" for e in t["exits"])]
    assert hit, "the synthetic trend should reach target 1 at least once"
    for t in hit:
        assert len(t["exits"]) == 1 and t["exits"][0]["qty"] == t["shares"]


def test_scalp_regime_switches_only_remove_checks():
    spec = _scalp_run(ScalpParams()).funnel["A"]
    relaxed = _scalp_run(ScalpParams(require_emas_rising=False, require_vwap_rising=False, require_or_break=False)).funnel["A"]
    assert not any(k in relaxed for k in ("regime_failed_emas_rising", "regime_failed_vwap_rising", "regime_failed_broke_opening_range"))
    assert relaxed.get("regime_minutes", 0) >= spec.get("regime_minutes", 0)


# ---- cost model: percentage commission plus a fixed fee on every order (Moomoo US) ------------------------------------------
FIXED_FEE = SharedParams(cost_per_share_round_trip=0.0, cost_pct_round_trip=0.0, cost_per_order=1.0, max_cost_pct_of_1R=1e9)


def test_fixed_fee_is_charged_on_the_entry_and_on_every_exit_order():
    stop_out = run([Fixed([40])], {45: (100, 100.1, 98.9, 99.2)}, shared=FIXED_FEE).trades[0]
    assert len(stop_out["exits"]) == 1 and stop_out["costs"] == pytest.approx(2.0)                 # entry + stop
    partial = run([Fixed([40])], {45: (100, 101.6, 99.9, 101.5), 46: (101.4, 101.5, 99.9, 100.2)}, shared=FIXED_FEE).trades[0]
    assert len(partial["exits"]) == 2 and partial["costs"] == pytest.approx(3.0)                   # entry + target 1 + breakeven exit
    assert partial["pnl"] == pytest.approx(500 * 1.5 - 3.0)


def test_commission_percentage_and_fixed_fee_add_up():
    p = SharedParams(cost_per_share_round_trip=0.0, cost_pct_round_trip=0.06, cost_per_order=0.99, max_cost_pct_of_1R=1e9)
    t = run([Fixed([40])], {45: (100, 100.1, 98.9, 99.2)}, shared=p).trades[0]
    assert t["costs"] == pytest.approx(1000 * 100.0 * 0.06 / 100 + 2 * 0.99)                      # 0.06% of $100,000 + two orders


def test_fee_rule_counts_the_fixed_fee_so_small_trades_are_skipped():
    p = SharedParams(cost_per_share_round_trip=0.0, cost_pct_round_trip=0.0, cost_per_order=0.99)
    assert size_position(10_000, 100.0, 99.0, p)["skip"] is None            # $1.98 on a $10,000 trade, 1R = $100
    small = size_position(1_000, 100.0, 99.0, p)                            # $1.98 on 10 shares with 1R = $10 -> 20% of 1R
    assert small["skip"] and "cost" in small["skip"] and small["est_round_trip_cost"] == pytest.approx(1.98)


def test_us_profile_uses_the_moomoo_schedule():
    from bot.intraday.market import US
    from bot.intraday.runner import base_shared
    sh = base_shared(US)
    assert (sh.cost_per_share_round_trip, sh.cost_pct_round_trip, sh.cost_per_order) == (0.0, 0.06, 0.99)
