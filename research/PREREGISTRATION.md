# Pre-registration: out-of-sample test on 2026-01-02 → 2026-08-02

Written 2026-10-05, **before** the January–July 2026 history was downloaded or looked at.

Everything below was found, tuned or noticed on US 1-minute history from **2026-08-03 → 2026-10-02** (KO, MU, IBM, SPY, AAPL, MSFT,
NVDA, QQQI, TSLA) plus META and ABBV for 2026-08-31 → 2026-10-02. The earlier period is new to every candidate, so it is a real
out-of-sample test. The candidates, their parameters and what they are expected to show are frozen here. Nothing is changed after the
result is seen; anything done afterwards is exploratory and the new data then counts as design data.

Costs: $0.02 per share round trip (a placeholder), the engine's fee rule (cost ≤ 10% of 1R), no event calendar, fills as in
`bot/intraday/backtest.py`. Universe: the 11 symbols above. Halves: the tradable days of the test period split at the middle
(the first day of the data only warms the indicators).

## Candidates and predictions

| # | Candidate (frozen) | Prediction | What counts as confirmed |
|---|---|---|---|
| P1 | Spec Scalp A, defaults, real engine | not demonstrated to be profitable | reported only; "good" needs the bar below |
| P2 | Spec Trend B and Range C, defaults, real engine | B ≈ 0, C < 0 | reported only |
| P3 | A pullback trigger (all triggers, 09:45–11:30, fee-eligible) vs random entries on the same symbol-day | alpha ≤ 0 | day-clustered 95% CI of alpha R excludes 0 on the negative side |
| P4 | Scalp exits "1R all-out + 15 min time stop" vs the spec exits, paired on the same A trigger pool | gain per trade > 0 | day-clustered 95% CI of the paired gain excludes 0 on the positive side |
| P5 | Opening-range breakout (E3) and high-of-day break (E4), alpha vs random entries | alpha < 0 | CI excludes 0 on the negative side |
| P6 | VWAP reversion long, k = 1.5 ATR(5m) below VWAP + bounce bar (E2a) | absolute R > 0 and alpha > 0 | both CIs exclude 0 on the positive side |
| P7 | VWAP reversion `ReversionX` defaults in the real engine | avg R > 0 | CI excludes 0 and the bar below is met |

Exact definitions are the code in `research/alpha.py` (`e1`–`e4`, `ReversionX`/`XD`), `research/scalp_lab.py` (`ScalpX`, `sim`) and
`bot/intraday/strategies.py`; the test itself is `research/frozen_test.py`.

## The bar for calling something a good strategy

All of these, in the real engine (one position, ≤ 3 trades a day, shared risk limits, fee rule):

1. at least 30 trades in the test period,
2. net average R above 0 with the day-clustered 95% confidence interval excluding 0,
3. profit factor ≥ 1.3,
4. the same sign of average R in both halves of the test period.

Eight predictions are made, so about one "confirmation" can be expected by luck at the 95% level. A single marginal result is not
evidence; a pattern that is consistent across the families (as the Aug–Oct results were) counts for more.

---

## Addendum (2026-10-05, still before the January–July data was analysed)

Two more candidates came out of further work on the 2026-08-03 → 2026-10-02 data. They are frozen here, before the new data is
examined; the first original fingerprint (260e5856…) covers everything above this line.

| # | Candidate (frozen) | Prediction | What counts as confirmed |
|---|---|---|---|
| P8 | `ReversionX(XD(k_atr=2.0))`, everything else default, real engine. **Selected because it was the best of 8 variants in-sample** (+0.11R, 30/30 orderings positive on Aug–Oct), while k=1.5 gave −0.12R and k=2.5 gave −0.01R, so that result is probably partly noise | avg R > 0 | the engine bar above |
| P9 | Depth effect: Spearman correlation between how far below VWAP the stock is (in ATR5 units) and the trade's net R, over all reversion events with depth ≥ 1.0 ATR (`e2x`, spec-like exits) | positive | day-clustered 95% CI of the correlation excludes 0 on the positive side (Aug–Oct gave +0.05 with CI [−0.00, +0.11], so this is not established) |

How the engine candidates (P1, P2, P7, P8) are reported: the engine breaks ties between simultaneous signals by the order of the
symbol list, which changes results, so each candidate is run with 30 random symbol orderings; the table shows the mean over orderings,
and the confidence interval comes from the trades of the first ordering.

Not carried forward, because they showed no benefit in-sample: Trend (B) with its regime filters removed (−0.01R, −1.0% against
+0.02R, +0.3% for the spec), time-of-day or SPY-direction filters for reversion (no consistent pattern).

## Addendum 2 (2026-10-05, still before the January–July data was analysed)

A robustness check on the Aug–Oct data found that the reversion alpha is not specific to the "ATR below VWAP" definition: other
oversold measures also showed positive alpha against random entries in both Aug–Oct halves (the random-entry baseline in such
neighbourhoods is itself strongly negative, so alpha overstates what is tradable; the absolute R was only +0.02 to +0.12). Two are
frozen as further checks of the same hypothesis:

| # | Candidate (frozen) | Prediction | What counts as confirmed |
|---|---|---|---|
| P10a | `e5("rsi", 30)`: 5-minute RSI(14) of today's closes below 30, then the same bounce bar, stop and spec-like exits as the other reversion events | alpha > 0 | day-clustered 95% CI of alpha R excludes 0 on the positive side |
| P10b | `e5("bb", 1.5)`: 5-minute close below its 20-bar mean minus 1.5 standard deviations, same bounce bar, stop and exits | alpha > 0 | same |

Their absolute results are printed for context but are not part of the decision rule.

## Addendum 3 (2026-10-05, still before the January–July data was analysed)

| # | Candidate (frozen) | Prediction | What counts as confirmed |
|---|---|---|---|
| P11 | Trend (B) entry timing: every B pullback trigger (regime filters off) inside 09:30–10:00+ window `30 ≤ elapsed ≤ 150`, stop 0.3–1.0% of price, fee-eligible, vs random 5-minute-bar entries on the same symbol-day (±30 minutes, same stop %, B's exits), `research.trend_lab.b_alpha_recs` | alpha ≤ 0 (Aug–Oct: −0.08R [−0.16, −0.01]) | day-clustered 95% CI of alpha R excludes 0 on the negative side |
