"""Read-only check of the Moomoo REST adapter against your SIMULATED account.

    python -m bot.smoke_moomoo              # accounts, cash, positions, price, candles (no orders)
    python -m bot.smoke_moomoo --order      # also place ONE 1-share simulated market order (SIMULATE only)
"""
import json
import sys
import time

from .brokers.moomoo_rest import MoomooError, MoomooRestBroker
from .models import Order
from .moomoo_oauth import AuthError, TokenStore


def step(name, fn):
    try:
        out = fn()
        print(f"[ok]   {name}: {out}")
        return out
    except (MoomooError, AuthError) as e:
        print(f"[FAIL] {name}: {e}")
        if isinstance(e, MoomooError) and e.extra:
            print("       raw:", json.dumps(e.extra)[:400])
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    return None


def main() -> int:
    tokens = TokenStore().load()
    if not tokens.get("refresh_token"):
        print("Not logged in. Run scripts\\moomoo-login.ps1 first.")
        return 1
    print(f"token file: {TokenStore().path}\ngranted scope: {tokens.get('scope')!r}\n")
    probe = MoomooRestBroker("SIMULATE", acc_id="probe")  # skips account discovery
    step("raw simulated accounts", lambda: probe._call("GET", "/api/v1.0/sim-trade/accounts"))
    step("raw authorized real accounts (read-only)", lambda: probe._call("GET", "/api/v1.0/accounts/authorized_trd_accs"))
    print()
    broker = step("connect + find US simulated account", lambda: MoomooRestBroker("SIMULATE"))
    if not broker:
        return 1
    print(f"       account id: {broker.acc_id}")
    step("cash", broker.cash)
    step("positions", lambda: [(p.symbol, p.qty, p.avg_price, p.last_price) for p in broker.positions()])
    print(f"       positions market filter that worked: {getattr(broker, '_pos_market', 'n/a')}")
    step("last price AAPL", lambda: broker.last_price("AAPL"))
    step("last 5 daily closes AAPL", lambda: broker.history("AAPL", 5))
    def intraday():
        from datetime import date, timedelta
        from .intraday.bars import is_regular
        bars = broker.intraday_bars("SPY", date.today() - timedelta(days=6), date.today())
        if not bars:
            return "NO BARS returned (weekend/holiday range, or history unavailable)"
        last_day = max(b.ts.date() for b in bars)
        reg = [b for b in bars if b.ts.date() == last_day and is_regular(b.ts)]
        pre = [b for b in bars if b.ts.date() == last_day and not is_regular(b.ts)]
        return (f"{len(bars)} bars, days={sorted({b.ts.date().isoformat() for b in bars})}; {last_day}: {len(reg)} regular "
                f"(first {reg[0].ts.time() if reg else None}, last {reg[-1].ts.time() if reg else None}), {len(pre)} extended-hours")
    step("1-minute SPY history (check: first regular bar should read 09:30, last 15:59)", intraday)
    if "--order" in sys.argv:
        o = step("place 1 share AAPL BUY via the adapter (marketable limit, simulated)",
                 lambda: broker.place_order(Order("AAPL", "BUY", 1)))
        if o:
            print(f"       status={o.status} id={o.id} limit_price={o.price} reason={o.reason!r}")
            time.sleep(2)
            step("positions after order", lambda: [(p.symbol, p.qty) for p in broker.positions()])
    broker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
