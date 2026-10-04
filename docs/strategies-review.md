# Review of the three-strategy spec (A scalp, B trend, C range)

Status of the code: strategies, shared risk layer and backtester are implemented and unit-tested on synthetic data.
**No result in this repo says anything about real markets yet.** Every threshold is your untested default.

## 1. Problems found in the spec (most important first)

1. **"Entry, stop and target as one bracket order" is not possible on this broker.**
   - Simulated account (`/sim-trade/.../orders`): order types are only `1=Limit` and `3=Market`. There is no stop type,
     no trigger price, no bracket. So paper trading can NOT hold stops at the broker.
   - Real account (`/accounts/.../orders`): `STOP` / `STOP_LIMIT` exist (with `aux_price`), but each call places one
     order. `multi_leg_info` is for option spreads. There is no OCO or bracket parameter.
   - Consequence: in simulation the bot must hold stops in memory (what the spec forbids), and for real trading the stop
     goes out as a second order after the entry fills, leaving a gap. Moving the stop to breakeven and selling 50% at T1
     means cancel/modify plus a new order.
   - Proposal for the paper engine: bot-held stops, clearly labelled; before any live money, test real stop orders
     with 1-share orders. Needs your decision before I build it.
2. **Backtest data is unverified.** The adapter pages Moomoo's `history-kline` (1-minute, extended hours, 370 bars per page).
   Depth of history, the paging contract and whether `time_key` is the bar start or end are untested. Run
   `python -m bot.smoke_moomoo` and check the "1-minute SPY history" line (first regular bar should read 09:30).
   The result screen lists days and bars actually received per symbol.
3. **Spread filter (`max_spread_pct_of_price`) cannot be backtested** (bars carry no bid/ask). It is applied only
   in live/paper mode, which is not built yet.
4. **Event days are not bundled.** FOMC/CPI/NFP/half-day dates are not in the repo (I could not verify them).
   Fill `data/calendar.json` (see `data/calendar.example.json`). Until then every backtest says so in its notes.
5. **The 0.5% risk rule rarely binds.** With a 1x notional cap, shares = min(0.5% equity / stop distance, equity / price).
   For stops under 0.5% of price the notional cap wins: A (stop <= 0.25%) risks <= 0.25% of the budget, B (0.3%-0.6%)
   risks 0.3%-0.5%. R-multiples are unaffected, but dollar P&L per R is smaller than the spec implies. Each trade logs
   which cap bound (`regime.sizing.binding`).
6. **100 paper trades is a weak test.** At a typical R spread (~1R standard deviation) 100 trades give an expectancy
   uncertainty of about +/-0.2R (95%). Only a large edge would show. The tab displays the 95% range for this reason.
   Strategies with strict filters (A, C) may need many months to reach 100.
7. **A and B overlap** on trend days and `max_open_positions=1` means whichever fires first blocks the other, so enabling
   both muddies attribution. Run them one at a time as the spec suggests. C never runs on a ticker after A/B traded it (and
   vice versa); implemented as a per-ticker, per-day family lock.
8. **Operating hours.** Regular session is 21:30-04:00 MYT during US daylight time and 22:30-05:00 during standard time
   (the bot uses America/New_York, so this follows automatically). It needs an always-on machine; a sleeping PC
   means no stops (see item 1).
9. **Not implemented:** the optional mega-cap with earnings-day skip (no earnings calendar source), and pattern-day-trader
   counting (depends on the account rules that apply to your Moomoo Malaysia account; check with Moomoo).

## 2. Where the spec was ambiguous: what I chose (all marked `INTERPRETATION` / `ASSUMPTION` in code)

- Indicators (EMA, ATR, volume SMA) carry across days; EMA20 on 5m needs 100 minutes. VWAP resets at 09:30. The first
  day of any backtest is warm-up only.
- A "key level" includes today's high of day. A's Target 2 is the nearest of HOD / prior-day high beyond T1, else 2.5R.
- A "pullback" is a run of qualifying 1m bars; only pullbacks #1 and #2 counted during the entry window with the regime on
  are tradable. A pullback stays valid for 10 one-minute bars (**my addition**, `pullback_max_age_bars`).
- A and B have no end-of-day rule in the spec for A; I flat everything at 15:55 (C at 15:30 as specified).
- B's `vwap_crosses <= 1` counts from 09:45; A's `<= 2` from 09:30.
- B's trigger must be the very next 5m bar after the pullback bar.
- C's kill rule also closes an open C position at that bar's close.
- With 1 share the "sell 50%" cannot split; the whole share exits at T1.
- Daily loss limit counts realized losses only. "Consecutive losses" and "trades per day" reset each day.
- `risk_per_trade_pct` and the notional cap apply to the strategy's budget, not the whole account.

## 3. Backtest fill model (conservative on purpose)

Signal at the trigger bar's close; a marketable limit lives for the next 1m bar only (fills at the open if open <= limit,
else at the limit if the bar trades down to it, else no trade). If one bar touches stop and target, the stop is assumed
first. A gap through a stop fills at the open. After T1 the breakeven stop is first checked on the next bar. Cost is
$0.02/share round trip (assumption, `cost_per_share_round_trip`). No lookahead (tested): truncating the future leaves
earlier trades unchanged.

## 4. Next phase (needs your decision on item 1)

Live/paper engine: 1m/5m bar feed from the broker, signals through the same risk layer, journal rows in
`strategy_trades`, reconcile open positions/orders on startup, 10-second stale-feed kill switch, stop handling per item 1.
