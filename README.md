# Trading Bot + Monitor App

Automated trading bot (Moomoo / Webull) with a cross-platform monitor app (iOS, Android, web).
Stack follows the Personal Assistant app: Expo SDK 57 / React Native 0.86 / expo-router / TypeScript, Supabase backend.

```
 Python worker (bot/)  ──service-role──▶  Supabase (Postgres + RLS + Realtime)  ◀──anon key + user JWT──  Expo app
 Strategy → Risk → Broker adapter          bot_status positions orders              Overview / Positions /
 pushes snapshot, orders, events           bot_events bot_commands                  Orders / Log / Settings
 polls bot_commands (halt/resume) ◀────────────────────────────────────────────────  Halt button inserts command
```

The broker connection cannot live in an Edge Function: Moomoo needs the OpenD gateway and the bot is a long-running
loop. So the bot is a worker you run on a machine of your own, and Supabase is the only thing the app talks to.

## Layout
```
app/             routes only (thin wrappers): (auth)/ login, sign-up, check-email; (tabs)/ 5 tabs
features/<x>/    auth, bot, settings: Context, screens/, calculations.ts (+ .test.ts), types.ts
lib/             supabase.ts, preferences.ts (AsyncStorage), providers/* (all network/native calls)
components/ constants/ hooks/
bot/             Python worker (brokers/, strategy/, risk.py, engine.py, sync.py)
supabase/migrations/   schema + RLS + realtime publication
```

## Status
| Piece | State |
|---|---|
| Engine, SMA-cross strategy, risk limits, kill switch | working, 4 pytest tests on the paper broker |
| Supabase sync + command channel (`bot/sync.py`) | written; unit-tested with a fake, **not run against a live project** |
| Migration | written, **not applied** |
| Expo app: auth gate, Face ID lock, 5 tabs, realtime + 15s fallback poll | typechecks, 5 jest tests, web bundle builds and the login route renders; **not tried on a device or against Supabase** |
| Moomoo REST adapter (`BROKER=moomoo_rest`, no OpenD) + OAuth 2.1/PKCE login | written from the docs, 9 tests against a mocked HTTP layer; **never called the live API** |
| Moomoo OpenD adapter (`BROKER=moomoo`) | written against `moomoo-api`; untested |
| Webull adapter | skeleton only |
| Strategies A/B/C, shared risk layer, backtester, worker | 33 new tests on synthetic data; **never run on real prices** |
| Strategy tabs in the app | rendered and exercised in a browser against a mocked backend; **not against your Supabase** |
| Paper engine for A/B/C (`LIVE_STRATEGIES=yes`) | 17 tests with a fake broker, matches the backtest on identical prices; **never run against the real simulated account**. Stops are held by the bot. |

## Setup
1. Create a Supabase project, apply `supabase/migrations/*.sql` (`npx supabase db push` or the SQL editor).
2. App: `cp .env.example .env` (URL + anon key), `npm install`, `npx expo start`. Sign up in the app.
3. Bot: `cp bot/.env.example bot/.env`; set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (bot host only) and
   `SUPABASE_USER_ID` (your user's id in Auth > Users). Then:
   ```
   python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
   set -a; . bot/.env; set +a; python -m bot.main
   ```
4. `npm test`, `npm run typecheck`, `pytest`.

Moomoo (REST, recommended, no OpenD): `BROKER=moomoo_rest`, then once on a machine with a browser:
Windows: `powershell -ExecutionPolicy Bypass -File scripts\moomoo-login.ps1` (nothing to install); or `node scripts/moomoo-login.mjs` (Node 18+); or `python -m bot.moomoo_login` (registers an OAuth client, prints an authorize URL, catches the callback on
`localhost:60355`, saves tokens to `~/.config/trading-bot/moomoo_tokens.json`, mode 0600). Access tokens last 2h and are
refreshed automatically; the refresh token is not rotated. Re-run the login if refresh ever fails.
`SIMULATE` uses Moomoo's `/sim-trade` endpoints; `REAL` uses `/accounts/...` and needs `ALLOW_LIVE=yes` plus
`TRADE_ENV=REAL`. REAL orders that moomoo wants confirmed (-2100/-2101) are never auto-confirmed; they show as REJECTED
with the confirm id.
Moomoo (OpenD SDK): run OpenD, `pip install -e '.[moomoo]'`, `BROKER=moomoo`.
Open questions to check on first run: refresh-token lifetime (not documented), and whether `/sim-trade` accepts the
Bearer token (the docs' curl examples show no auth header).

iOS device build follows the Personal Assistant flow (`npx expo prebuild`, bundle id `com.trex921.trading-monitor`,
then xcodebuild/devicectl, or `eas build`). Android package: `com.trex921.tradingmonitor`.

## Strategies (A scalp, B trend, C range)
Tabs: Scalp / Trend / Range, each with a budget (capped at the account balance, enforced in the app and by a database
trigger), a backtest runner, and a paper-trade journal. Backtests are queued from the app and executed by the bot worker
(needs `BROKER=moomoo_rest`). Offline: `python -m bot.intraday.cli --strategy ALL --synthetic` or `--csv-dir data`.
Read `docs/strategies-review.md` first: it lists what the spec could not do on this broker and every assumption made.
New DB objects: `supabase/migrations/20261005000000_strategies.sql` and `20261006000000_engine_status.sql` (apply with `supabase db push`).
Rules (v2): 1% risk with a hard 1% ceiling, 2% daily loss, drawdown breakers at 6% (risk 0.5%) and 10% (stop); see the top of `docs/strategies-review.md`.
Windows needs the time-zone database: `python -m pip install tzdata` (or `pip install -e .`, which installs it).

## Notes
- `web.output` is `single` (SPA): static rendering runs in Node, where AsyncStorage/Supabase auth has no `window`.
- Halt is eventually consistent: the app inserts a `bot_commands` row, the worker claims it within
  `COMMAND_POLL_SECONDS` (default 3s). If the worker is down the Overview shows "BOT OFFLINE?" (no snapshot for 3 min).
- Positions are replaced wholesale on each snapshot so closed positions disappear.

## Roadmap
1. Apply migration, run end to end on the paper broker, then Moomoo SIMULATE.
2. Fill reconciliation and order status polling; persist day-start equity across restarts.
3. Push alerts (Expo notifications via an Edge Function triggered from `bot_events` with level ERROR/WARN).
4. Webull adapter; backtester; more strategies; AI assistant tab only if wanted.

**Not financial advice.** The sample strategy is a demo, not an edge.
