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
    if "--order" in sys.argv:
        px = broker.last_price("AAPL")
        limit = f"{px * 1.01:.2f}"  # limit slightly above market: the docs say these fill immediately
        tried = set()
        for market in (broker.sim_market, 2):
            for kind, extra in (("MARKET", {"order_type": 3}), ("LIMIT", {"order_type": 1, "price": limit})):
                if (market, kind) in tried:
                    continue
                tried.add((market, kind))
                body = {"market": market, "symbol": "AAPL", "order_side": 1, "qty": "1", **extra}
                r = step(f"sim order market={market} {kind}", lambda: broker._call(
                    "POST", f"/api/v1.0/sim-trade/{broker.acc_id}/orders", json=body))
                if r:
                    print(f"       ACCEPTED with market={market} type={kind}: {r}")
                    time.sleep(2)
                    step("positions after order", lambda: [(p.symbol, p.qty) for p in broker.positions()])
                    broker.close()
                    return 0
        print("       No variant accepted. Paste this whole output.")
    broker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
