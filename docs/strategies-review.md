# Review of the three-strategy spec (A scalp, B trend, C range)

## Current rules (v2, from your update)
| Rule | Value |
|---|---|
| Risk per trade | 1.0% of the strategy's budget; **hard ceiling 1.0%: the bot refuses to start above it** |
| Daily max loss | 2.0% of the capital allocated to enabled strategies (two full losses), then no more entries that day |
| Max position | 1x the budget (no leverage), max 3 trades/day, stop after 2 consecutive losses, 1 open position |
| Strategy B stop band | 0.3% to 1.0% of price (was 0.3% to 0.6%) |
| Drawdown breaker 1 | 6% from the peak of (budget + realized P&L): risk per trade drops to 0.5% |
| Drawdown breaker 2 | 10% from peak: live trading disabled, back to paper |
Sizing: `shares = floor(min(equity*0.01/(entry-stop), equity*1.0/entry))`, logged on every trade.

Breaker interpretation: a tripped breaker clears only when realized P&L makes a new peak. In a backtest, breaker 2 stops
all further simulated entries (the account would be back on paper). The paper engine keeps paper trading and shows the
breaker on screen. All of these are parameters in `bot/intraday/params.py` (`SharedParams`).

**Effect of the 1x cap at 1% risk:** risk as a share of the account can never exceed the stop distance as a share of
price. A (stops <= 0.25%) therefore risks at most 0.25% per trade, C depends on the range width, and only B (stops up
to 1%) can actually use the full 1%.

Status of the code: strategies, shared risk layer and backtester are implemented and unit-tested on synthetic data.
**No result in this repo says anything about real markets yet.** Every threshold is your untested default.

## Markets: US, Singapore (SGX), Malaysia (Bursa)
You choose which markets run in the app: **Settings > Markets** (switch, 1-3 symbols, and a paper balance for SG/MY).
Budgets, backtests, engine status and the paper journal are all per market, in that market's currency.

| | US | SG | MY |
|---|---|---|---|
| Hours (local, **unverified for SG/MY**) | 09:30-16:00 | 09:00-12:00, 13:00-17:00 | 09:00-12:30, 14:30-17:00 |
| Trading minutes | 390 | 420 | 360 |
| Lot size | 1 | 100 | 100 |
| Paper orders | Moomoo simulated account | bot-side simulation, real quotes | bot-side simulation, real quotes |
| Backtests | yes | yes (if Moomoo serves 1-minute history) | yes (same caveat) |
| Fee assumption (placeholder) | $0.02/share | 0.15% round trip | 0.30% round trip |

All windows are expressed in **trading minutes since the open**, so a lunch break never distorts them: entry windows
are minutes 15-120 (A), 30-150 (B) and 60-300 (C), opening range = first 15 minutes, initial balance = first 60 minutes,
flat 5 minutes before the close (C: 30). On a US day these are exactly the original clock times. Time stops also count
trading minutes only. 5-minute bars never straddle the lunch break.

**What I found that you should know:**
1. **Fees probably make these strategies untradeable on SGX and Bursa.** The spec skips a trade when costs exceed 10%
   of 1R. At 0.30% round-trip costs that needs a stop of 3% or more; at 0.15%, 1.5%. A's stops are at most 0.25% and
   B's at most 1%. With the placeholder fees, backtests on MY/SG take **zero trades** and report "cost rule" skips.
   Real fees may be lower, so put your actual numbers in `data/markets.json` (`cost_pct_round_trip`) before concluding
   anything. Stamp duty and clearing fees are charged on both sides.
2. **No Moomoo simulator exists for SGX or Bursa**, only HK, US, US options and Canada. So SG/MY paper trading is run
   by the bot itself (`bot/brokers/local_paper.py`): real bid/ask quotes, simulated fills at the ask/bid, whole lots only.
   That is optimistic (no queue, no partial fills, no impact), so treat it as an upper bound.
3. **Hours are my best knowledge, not verified.** `python -m bot.smoke_moomoo` now prints the hours actually present in
   Moomoo's data for SG and MY and says whether they match. Correct `data/markets.json` if not.
4. **Thin liquidity.** A quote older than 60 seconds counts as stale for SG/MY (10 s for US), because many local
   stocks trade less often than that. Spreads and tick sizes are much wider than SPY/QQQ; `entry_offset` (0.02) and
   `stop_buffer_min` are in price units and may need scaling per stock. Tick sizes are not enforced.
5. **Event calendar** is per market (`"markets": {"MY": {"blocked_days": [...], "half_days": [...]}}`). Exchange holidays
   need no entry (no bars = no trading). Moomoo's 1-minute history for SG/MY is unverified (depth, completeness).
6. **Operating hours are friendlier:** Bursa and SGX run during your day (09:00-17:00), so the PC does not need to
   stay on overnight for these two. Same warning applies: if it sleeps, nothing trades and bot-held stops do nothing.
7. **First real probes (your account), still unresolved:** Singapore history was refused with *"realtime quote permission
   required"* (no SGX real-time quote right: enable or buy it in the Moomoo app). Malaysia was first refused as
   *"unsupported market"* and, a later run, as *"invalid symbol"*. The symbol-info endpoint lists MY as supported while the
   history endpoint's list omits it, so the API is inconsistent and I cannot yet say whether Bursa data is obtainable.
   `python -m bot.smoke_moomoo --probe MY.1155 SG.D05` now asks Moomoo which codes it recognises (and their real board lot).
   Until a code returns 1-minute bars, Bursa can only be backtested from your own CSV files:
   `python -m bot.intraday.cli --market MY --csv-dir data/my --tickers 1155` (columns `timestamp,open,high,low,close,volume`;
   naive timestamps are read as Malaysia time).
8. The mega-cap / ETF suggestions (`ES3` for SG, `1155` for MY) are only sample symbols, not recommendations.

## 1. Problems found in the spec (most important first)

1. **"Entry, stop and target as one bracket order" is not possible on this broker.**
   - Simulated account (`/sim-trade/.../orders`): order types are only `1=Limit` and `3=Market`. There is no stop type,
     no trigger price, no bracket. So paper trading can NOT hold stops at the broker.
   - Real account (`/accounts/.../orders`): `STOP` / `STOP_LIMIT` exist (with `aux_price`), but each call places one
     order. `multi_leg_info` is for option spreads. There is no OCO or bracket parameter.
   - Consequence: in simulation the bot must hold stops in memory (what the spec forbids), and for real trading the stop
     goes out as a second order after the entry fills, leaving a gap. Moving the stop to breakeven and selling 50% at T1
     means cancel/modify plus a new order.
   - **Decision taken: paper trading uses bot-held stops, labelled on screen.** The paper engine (`bot/intraday/live.py`)
     watches the price every ~5 seconds and sends the exit itself. If the bot, the PC or the internet is down, nothing
     protects an open position. Before any live money, stops must live at the broker (test real STOP orders with
     1-share orders first). The engine refuses REAL accounts.
2. **Backtest data is unverified.** The adapter pages Moomoo's `history-kline` (1-minute, extended hours, 370 bars per page).
   Depth of history, the paging contract and whether `time_key` is the bar start or end are untested. Run
   `python -m bot.smoke_moomoo` and check the "1-minute SPY history" line (first regular bar should read 09:30).
   The result screen lists days and bars actually received per symbol.
3. **Spread filter (`max_spread_pct_of_price`) cannot be backtested** (bars carry no bid/ask). It is applied only
   in live/paper mode, which is not built yet.
4. **Event days are not bundled.** FOMC/CPI/NFP/half-day dates are not in the repo (I could not verify them).
   Fill `data/calendar.json` (see `data/calendar.example.json`). Until then every backtest says so in its notes.
5. **The risk percentage is limited by the 1x cap.** shares = min(risk% x equity / stop distance, equity / price). When
   the stop is closer than the risk percentage (stop% < risk%), the notional cap wins and the trade risks only the stop
   distance. At 1% risk that covers all of A and most of C. Each trade logs which cap bound (`regime.sizing.binding`).
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

## 4. The paper engine (built; untested against the real simulated account)

Run with `LIVE_STRATEGIES=yes`, `BROKER=moomoo_rest`, `TRADE_ENV=SIMULATE` in `bot\.env`. It:
- polls quotes every ~5s and 1-minute bars once a minute, feeding the same strategy and risk code as the backtester
  (a test checks it makes the same trade as the backtest on identical prices);
- takes entries as buy limits at the signal price (up to 20s to fill, otherwise cancelled), exits with marketable
  limit sells (retrying at 0.1%, 0.2%, 0.4% below the last price);
- on start-up reconciles broker positions/orders with its saved state (`data/live_state.json`): leftover orders on its
  tickers are cancelled; an unexpected or mismatched position halts new entries and says why;
- stale feed (quote older than 10 s): no entries, open orders cancelled, status shows that bot-held stops are not being
  watched; recovers by itself. Any rejected order: entries halt until the bot is restarted;
- carries yesterday's position out at the open if the bot missed the end-of-day exit;
- journals closed trades to `strategy_trades` (and `data/journal.jsonl`), publishes `engine_status` for the app.
Costs in paper trades are the assumed $/share, because the simulator reports no fees.

**Operating requirement:** all of this runs on the PC that runs the bot. If it sleeps or goes offline, there are no
trades and no stop protection.
