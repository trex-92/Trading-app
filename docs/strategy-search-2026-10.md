# Strategy search, October 2026

Question: can the three strategies (A Scalp, B Trend, C Range) be changed to win more often and make more money, and do other day-trading
ideas (gap fill, gap and go, relative strength, last-hour momentum) work? Data: US 1-minute history, 2026-01-02 → 2026-10-02
(189 trading days) for KO, MU, IBM, SPY, AAPL, MSFT, NVDA, QQQI, TSLA, plus META and ABBV. Costs: the placeholder $0.02 per share
round trip, the engine's fee rule, no event calendar. Code and how to rerun it: `research/`. Both rounds of testing were written down
before the data that judges them was looked at: `research/PREREGISTRATION.md` and `research/PREREGISTRATION_2.md`; full outputs are in
`research/results/`.

## Short version

* **Nothing here proves an edge, and none of the three original strategies is shown to be profitable.** On 145 days that were never used to
  build anything, Scalp lost (−0.11R over 32 trades), Range lost (−0.13R), and Trend was positive but not significant (+0.12R over 105
  trades, interval −0.06 to +0.30, second half negative).
* **Buying strength loses to random entries, and that replicated strongly**: the Scalp pullback trigger (−0.06R), opening-range
  breakout (−0.09R), high-of-day breakout (−0.21R) and the Trend pullback (−0.10R), each with an interval entirely below zero.
  Relative-strength "leaders" and last-hour momentum failed in the locked period as well.
* **Buying an oversold stock beats random entries, but is not profitable by itself**: positive alpha in every definition tried
  (VWAP depth, RSI, Bollinger), absolute result about zero after costs.
* **Gap fill is the one new idea that held up in the locked period** (buy a stock that gapped down 1–4%, enter in the late morning,
  target the prior close). One formula met the pre-registered bar (35 trades, 71% win rate, +0.24% per trade, profit factor 2.8). The
  evidence is promising but not conclusive: the two symbols never used in tuning were negative over January–July, and on all
  never-tuned data pooled the result is +0.05% to +0.14% per trade with an interval that includes zero.
* **Win rate and profit are different things.** The high-win-rate gap-fill formula wins about 59–71% of the time, but its profit factor
  on untuned data is only 1.18. A formula can win often and still barely make money.
* **Ten or so trades prove nothing.** At a per-trade spread of about ±1.1R, confirming a real +0.1R edge takes about 950 trades,
  +0.2R about 240, +0.3R about 105. Gap fill produces about 0.8 filled events per trading day across 11 symbols (about 0.45 once the engine's
  one-position and three-trades-a-day rules apply), so a few hundred paper trades is a matter of months.

## How the testing was done

Backtests on a few weeks of data are dominated by luck and by the market's direction, so the harness (1) simulates every candidate
entry, not just the few a strategy would take; (2) compares each entry with random entries on the same symbol-day ("alpha"), which
removes the day's drift; (3) splits by date, keeps symbols and a final period locked away, counts every variant tried and applies
White's reality check; (4) validates its simulator against the repo's real engine (0 mismatches on every family and exit style); and
(5) averages engine results over random symbol orderings, because the engine breaks ties by list order. The market itself changed
character along the way (a March selloff, strong April–May, a June pullback, a flat-to-up summer), so no single month is typical.

## Round 1: the three strategies and their variants

On 2026-08-03 → 2026-10-02 (43 days, tuned and explored heavily, about a thousand comparisons) the work found the results below. They
were then fixed in writing and tested once on 2026-01-02 → 2026-08-02 (145 days, never used).

| claim from Aug–Oct | result on Jan–Jul | verdict |
|---|---|---|
| spec Scalp is not demonstrably profitable | 32 trades, −0.11R, profit factor 0.81, −1.3% | holds; Aug–Oct's +0.69R over 6 trades was luck |
| Trend about 0, Range negative | Trend 105 trades +0.12R, PF 1.37, +5.6%; Range −0.13R | Trend positive but not significant; Range loses |
| buying strength underperforms random entries | Scalp trigger −0.06R [−0.11, −0.02], opening-range breakout −0.09R [−0.13, −0.06], high-of-day break −0.21R [−0.23, −0.18], Trend pullback −0.10R [−0.12, −0.06] | **confirmed** |
| an oversold bounce beats random entries | alpha +0.15R (VWAP depth), +0.28R (RSI), +0.34R (Bollinger), all intervals above 0 | confirmed |
| …and is profitable in absolute terms | −0.04R (n = 5,419), −0.03R, +0.02R | **not confirmed** |
| reversion in the engine, k = 2.0, was positive | 369 trades, +0.05R, PF 1.09, +1.4% (orderings −2.1% to +6.0%) | shrank to about zero; k = 1.5 lost −8% |
| deeper below VWAP is better | rank correlation −0.002 | not confirmed |
| taking everything off at 1R and cutting after 15 minutes improves returns | paired gain −0.004% per trade (the win rate does rise, 41.6% to 48.1%) | not confirmed |

Costs matter more than any rule tweak: gross of fees almost every setup is about zero, and at stops under 0.15% the $0.02 per share costs
about 0.14R per trade. At that fee a stock needs to be priced above roughly $130 for a typical 0.15% stop to pass the fee rule.

## Round 2: gap fill, gap and go, relative strength, last hour

A staged search tuned parameters and added extra signals (SPY direction, relative volume, opening inside or outside yesterday's
range, yesterday's closing strength, distance from 20-day extremes, stock volatility, and others) using only 2026-01-02 → 2026-07-31
and the nine main symbols. The selection rule was fixed in code beforehand; 1,239 variants were tried (gap fill 257, gap and go 318,
relative strength 411, last hour 253). Reality-check p-values over all variants of a family were 0.074, 0.052, 0.252 and 0.074, so no
family was clearly significant on the design data. Each family produced a profit-oriented formula (core, and E with extra signals) and a
highest-win-rate formula (W); all were frozen and run once on 2026-08-03 → 2026-10-02 for all 11 symbols:

| formula | design (n, win, return per trade) | **locked final period** (n, win, return per trade [95% interval], PF) | result |
|---|---|---|---|
| GF-core (gap fill) | 122, 47%, +0.19% | 35, 51%, +0.25% [−0.00, +0.52], 1.87 | borderline |
| GF-E | 60, 53%, +0.39% | 9, 33%, +0.33% | too few trades |
| **GF-W** | 125, 61%, +0.15% | **35, 71%, +0.24% [+0.08, +0.43], 2.83** | **meets the bar** |
| GG-core (gap and go) | 148, 60%, +0.20% | 53, 40%, −0.03% [−0.24, +0.18] | fails |
| GG-E, GG-W | 75, 61% / 50, 66% | 26, 38%, −0.04% / 13, 69%, +0.29% | fails / too few |
| RS-core (relative strength) | 220, 52%, +0.12% | 97, 33%, −0.13% [−0.23, −0.01], 0.64 | fails, significantly negative |
| RS-E | 64, 61%, +0.26% | 39, 33%, −0.12% | fails |
| LH-core (last hour) | 264, 61%, +0.08% | 91, 36%, −0.02% | fails |
| LH-E | 101, 69%, +0.13% | 46, 33%, −0.04% | fails |

The plain, untuned versions of each idea, for comparison: buy gap-downs ≥ 1% at 09:45 +0.06% (n = 34), gap-ups ≥ 1% on the first
opening-range break −0.34% (n = 18), a stock ≥ 0.5% ahead of SPY at 10:30 −0.06% (n = 171), last-hour momentum (up ≥ 0.6% and above
VWAP at 15:00) −0.06% (n = 107, win rate 46%). Last-hour *momentum* never worked; the last-hour candidates are the oversold-bounce
version, and those failed too. The bar was: at least 20 trades, a 95% interval for the return per trade entirely above 0, win rate
at least 45%, profit factor at least 1.3.

**The gap-fill formulas, in words.** GF-W: when a stock opens 1–4% below yesterday's close, buy it between about 10:30 and 11:45
(no further confirmation); stop 2.5 five-minute ATRs below entry; sell half halfway back to yesterday's close and the rest at it; out
at 13:30. GF-core: the same entry, stop just below the day's low, everything sold at yesterday's close, out at 13:30.

**How far to trust it.** For: on the nine main symbols the later period held up (GF-core +0.30%, GF-W +0.20% per trade); all eight
neighbouring parameter settings were positive on the locked period (+0.07% to +0.30%); in the real engine GF-W made 19 trades with a
69% win rate, profit factor 3.2 and +4.7% over the 43 days (orderings +3.6% to +6.7%). Against: the two symbols never used in tuning
were negative over January–July (GF-core −0.07% over 20 trades, GF-W −0.26% over 21); pooling everything never used for tuning gives
GF-core +0.14% per trade [−0.08, +0.36] over 55 trades and GF-W +0.05% [−0.15, +0.24] over 56; GF-W lost money in January (−0.27%)
and June (−0.50%); 1,239 variants were searched and ten formulas tested at the end, so one pass is weak evidence. It is a candidate for paper
trading, not a proven strategy.

## What to do next

1. If you want to pursue gap fill, paper trade it. About 0.8 filled events per trading day appear across 11 symbols (about 0.45 per
   day once the engine's one-position and three-trades-a-day rules apply), so a few hundred paper trades takes months, and that is
   the only test that can settle it. It is not implemented as a bot strategy yet.
2. Replace the placeholder fee with the real one; fees decide which stocks can trade at all.
3. More symbols and more history (a year or two) are the cheapest way to raise the confidence of every number above.
4. Do not trade the relative-strength, gap-and-go, last-hour or breakout ideas on this evidence, and do not run Range.

## Changes made to the repo

* `ScalpParams.t1_frac` (default 0.5 = unchanged) and three regime switches `require_vwap_rising`, `require_emas_rising`,
  `require_or_break` (default on = unchanged), with tests. No strategy default was changed, because nothing here showed that a change helps.
* The funnel splits "stop wider than the strategy allows" into how far over the limit each rejected stop was.
* **Bug fix:** the history cache never saved the last day of a requested range (a 960-bar day counts as a full page). Fixed in
  `bot/brokers/moomoo_rest.py`, with two regression tests that fail without the fix.
* `tests/conftest.py`: the tests no longer depend on a developer's real `bot/.env` (they failed whenever one existed).
* `research/`: the harness, downloader, day-level lab, four strategy families, staged search, the two pre-registrations with their
  scripts, and the full outputs in `research/results/`.
