# Pre-registration 2: gap fill, gap and go, relative strength, last hour

Written 2026-10-05, **before** the locked final period (2026-08-03 → 2026-10-02) or the two hold-out symbols' final-period results
were used for anything in this work. The formulas are in `research/new_formulas.json` (sha256 `b991829204e5a728…`), generated from the
search output, not typed by hand.

## What was done on the design data

Design data: US 1-minute history 2026-01-02 → 2026-07-31 (145 trading days), KO, MU, IBM, SPY, AAPL, MSFT, NVDA, QQQI, TSLA.
META and ABBV were removed from the search, and the locked final period was physically excluded from the data it received.

`research/search_new.py` ran a staged search per family with a selection rule fixed in code beforehand (among variants with enough
trades, a positive mean and a positive mean in at least 3 of the 4 time blocks, take the highest day-clustered t-statistic of the mean
return per trade). Variants evaluated: gap fill 257, gap and go 318, relative strength 411, last hour 253 (1,239 in total). White's
reality check over all variants of a family gave adjusted p-values of 0.074 (gap fill), 0.052 (gap and go), 0.252 (relative strength)
and 0.074 (last hour): none is clearly significant after allowing for the size of the search, which is why the final period decides.

The simulator behind every number was verified against the repo's real engine (0 mismatches on all four families and every exit
style). Costs are the placeholder $0.02 per share and the engine's fee rule. Returns are account returns with the engine's sizing
(all-in at 1× notional unless the stop is wider than 1%).

## Frozen candidates (parameters in the JSON; in words, times are US Eastern)

| id | what it does | design result (n, win rate, return per trade) |
|---|---|---|
| GF-core | buy a stock that opened 1–4% below yesterday's close, entering between about 10:30 and 11:45 with no confirmation; stop 0.1 ATR(5m) below the day's low; sell everything at yesterday's close; flat at 13:30 | 122, 47%, +0.19% |
| GF-E | GF-core, only when the stock is at least 8% below its 20-day high, its daily ATR is at least 2% of price and the 5-minute EMA9 is below EMA20 | 60, 53%, +0.39% |
| GF-W | gap-down 1–4%, same window; stop 2.5 ATR(5m) below entry; half off halfway to yesterday's close, the rest at yesterday's close; flat 13:30 | 125, 61%, +0.15% |
| GG-core | stock gapped up ≥ 0.5%; buy when it closes above the first-15-minute high and above VWAP (within the first hour); stop = lower of VWAP and the last 5 lows, minus 0.1 ATR; half off at 1R, the rest when a 5-minute bar closes below VWAP; flat 13:30 | 148, 60%, +0.20% |
| GG-E | GG-core, only when cumulative volume is at least the 20-day average at that minute | 75, 61%, +0.28% |
| GG-W | GG-core, only when price is below yesterday's high | 50, 66%, +0.20% |
| RS-core | from 11:30, buy a stock at least 1.0% ahead of SPY since the open and above VWAP; stop = lower of VWAP and the last 10 lows, minus 0.1 ATR; half off at 1.5R, rest at 2.5R; time stop 180 minutes | 220, 52%, +0.12% |
| RS-E | RS-core, only when SPY is below its VWAP and the stock's daily ATR is at least 2% | 64, 61%, +0.26% |
| LH-core | at 15:30 buy a stock at least 2 ATR(5m) below VWAP while SPY is down on the day; stop 0.8% below entry; half off at 1R; out by 15:55 | 264, 61%, +0.08% |
| LH-E | LH-core, only when price is above yesterday's low, below yesterday's high and at least 0.5% below the day's high | 101, 69%, +0.13% |

Last-hour **momentum** (buying strength into the close) was part of the search; it did not survive (plain versions earned −0.03% to
−0.04% per trade), so the last-hour candidates are the oversold-bounce version. That is a finding, not a choice made afterwards.

Reference ideas, reported but never judged (the plain versions of each idea, with no tuning): `ref-GF-plain`, `ref-GG-plain`,
`ref-RS-plain`, `ref-LH-momentum`.

## What is run on the locked period, once

`python -m research.final_new` evaluates every candidate on 2026-08-03 → 2026-10-02 for all 11 symbols at the event level, plus: the
nine main symbols and the two hold-out symbols separately, the same formula on the hold-out symbols over the design dates, the real
engine with 30 random symbol orderings, and the result for parameter values one step away from each formula.

## Decision rule

A candidate **meets the bar** only if, on the final period and all 11 symbols, all of: at least 20 trades; the day-clustered 95%
confidence interval of the return per trade is entirely above 0; win rate at least 45%; profit factor (in R) at least 1.3. The
other results (engine view, hold-out symbols, neighbouring parameters) are reported and read as supporting or undermining evidence,
but do not change whether the bar is met.

Ten candidates are tested, in four nested families, so roughly 0.1 to 0.25 passes can be expected by luck if none has an edge. One
marginal pass is therefore weak evidence; passes across the formulas of a family and across its neighbouring parameters count for more.
No formula or parameter is changed after the result is seen. Anything done afterwards is exploratory and the final period then counts as
design data.
