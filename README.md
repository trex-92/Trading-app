# Trading Bot + iOS Monitor

Automated trading bot (Moomoo / Webull) with a SwiftUI iOS app to monitor it.

```
 ┌────────── Python bot (this repo: bot/) ──────────┐        ┌────────────┐
 │ Strategy → RiskManager → Broker adapter → orders │        │ iOS app    │
 │            Engine loop  → SQLite store           │◀─HTTPS─│ (ios/)     │
 │            FastAPI (bearer token) ───────────────│        └────────────┘
 └──────────────────────────────────────────────────┘
        Broker adapters: paper (works), moomoo (via OpenD), webull (skeleton)
```

## Status
| Piece | State |
|---|---|
| Engine, SMA-cross strategy, risk limits, kill switch, SQLite log | working, tested on the paper broker |
| Monitor API (`/status /orders /events /halt /resume`) | working, tested |
| Moomoo adapter | written against `moomoo-api`; **untested against a real OpenD** |
| Webull adapter | skeleton only (needs OpenAPI approval + SDK verification) |
| iOS app (dashboard, orders, log, halt button) | source written; **not compiled** (no Xcode here) |

## Run the bot (paper)
```
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # set API_TOKEN
set -a; . ./.env; set +a
python -m bot.main
pytest
```

## Moomoo
Install and log in to OpenD, `pip install -e '.[moomoo]'`, set `BROKER=moomoo`, keep `TRADE_ENV=SIMULATE`
until you have watched it for a while. Real trading needs both `TRADE_ENV=REAL` and `ALLOW_LIVE=yes`.

## iOS app
In Xcode: New Project → iOS App (SwiftUI) named `TradingMonitor`, delete the generated Swift files, add the
files in `ios/TradingMonitor/`. Set Server URL and token in the Settings tab. Reach the bot over a
private tunnel (Tailscale) or an HTTPS reverse proxy; don't expose the port plain on the internet
(plain `http` also needs an ATS exception).

## Roadmap
1. Verify Moomoo adapter on simulated account; add order-status polling/fill reconciliation.
2. Webull adapter.
3. Backtester + more strategies; persist day-start equity across restarts.
4. APNs push alerts (fills, blocked orders, errors, loss limit); Live Activities.
5. Per-device tokens / TLS.

**Not financial advice.** The sample strategy is a demo, not an edge. Test in simulation first.
