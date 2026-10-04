# research/

A small harness for asking "does this strategy have an edge?" on the 1-minute history in `data/cache`. It only reads the cache and
runs the repo's own backtester; it never trades or calls the broker (except `download.py`, which makes read-only quote requests).
Run everything from the repo root with the venv active. The write-up of what it found is `docs/strategy-search-2026-10.md`.

| file | what it does |
|---|---|
| `download.py` | fills `data/cache` for a date range (resumable, skips cached days): `python -m research.download --symbols KO,MU --start 2026-01-01 --end 2026-10-02` |
| `common.py` | reads the cache, picks symbols, sets the design / holdout split, `engine()` runs a strategy object in the real `Backtester` |
| `scalp_lab.py` | `ScalpX` (same logic as `Scalp`, with switches and feature capture; verified to give identical trades), `sim()` (one trade at a time with the engine's fill and exit rules), bootstrap helpers |
| `trend_lab.py` | the same for Trend (B): `TrendX`, engine-based event runs, `sim_b()` |
| `alpha.py` | the random-entry alpha test, the alternative entry families (`e1`–`e4`, `e2x`) and the VWAP-reversion strategy `ReversionX` |
| `ridge.py` | out-of-sample "do these features predict R?" check (pure-python ridge regression) |
| `reality_check.py` | White's reality check over a grid of variants (day-block bootstrap) |
| `run_all.py` | prints the whole Aug–Oct analysis; `--start/--end/--design-end` choose the window |
| `daylab.py` | per symbol-day arrays (1-minute, no look-ahead) with daily context (prior day, gap, premarket volume, trailing volume and range), `simulate()` (engine-faithful trade simulator, verified against the engine with 0 mismatches) and `Replay` (pushes event-level signals through the real engine) |
| `families.py` | event generators for four long-only families: gap fill (`GF`), gap and go (`GG`), relative strength vs SPY (`RS`), last-hour momentum (`LH`), plus the library of extra signals |
| `search.py`, `search_new.py` | the staged formula search (design data only, fixed selection rule, every variant counted, White's reality check) |
| `diagnose_new.py` | design-period stress tests of the chosen formulas (concentration, neighbouring parameters, the engine view, the plain versions of each idea) |
| `results/` | the full text output of every run quoted in the write-up |
| `final_new.py`, `PREREGISTRATION_2.md`, `new_formulas.json` | the frozen formulas and the one-shot evaluation on the locked final period and the hold-out symbols |
| `PREREGISTRATION.md`, `frozen_test.py` | the candidates, predictions and pass/fail rules written down before the January–July data was looked at, and the script that tests them |

## The methods in one paragraph

Backtests on a few weeks of data are dominated by luck and by the market's direction, so the harness (1) simulates every candidate
entry, not just the few the strategy would take, giving hundreds or thousands of trades instead of ten; (2) compares each entry with
random entries on the same symbol-day, same time of day, same stop distance and same exits, which removes the day's drift ("alpha");
(3) splits by date into a design period and a holdout, keeps symbols that were never used for a final check, and counts how many
variants were tried (White's reality check); (4) validates its per-trade simulator against the real engine; and (5) for engine results,
averages over random symbol orderings because the engine breaks ties by list order.

## Things to keep in mind

* Costs are the placeholder $0.02 per share round trip. Fees decide the result for tight stops and cheap stocks.
* No event calendar is applied (FOMC, CPI/NFP, half days), and spread is not modelled beyond the placeholder cost.
* Every result is on one period of one market. A pattern that holds in two periods and on unseen symbols is worth testing further;
  it is not proof.
* Rerun `run_all` after downloading more history; with a year or two of data the statistics get much sharper
  (see the power numbers in the write-up: confirming a +0.1R edge takes about a thousand trades).
