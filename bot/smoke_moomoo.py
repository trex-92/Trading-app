"""Read-only check of the Moomoo REST adapter against your SIMULATED account.

    python -m bot.smoke_moomoo              # accounts, cash, positions, prices, history, SG/MY data (no orders)
    python -m bot.smoke_moomoo --order      # also place ONE 1-share simulated market order (SIMULATE only)
    python -m bot.smoke_moomoo --probe MY.1155 SG.D05    # check specific symbol codes (put --probe last)
"""
import json
import sys
import time
from datetime import date, datetime, timedelta

from .brokers.moomoo_rest import MoomooError, MoomooRestBroker
from .intraday.market import MY, SG, US, configured_runs, explain_error, observed_sessions
from .models import Order
from .moomoo_oauth import AuthError, TokenStore


def step(name, fn, out=print):
    try:
        res = fn()
        out(f"[ok]   {name}: {res}")
        return res
    except (MoomooError, AuthError) as e:
        out(f"[FAIL] {name}: {e}")
        if isinstance(e, MoomooError) and e.extra:
            out("       raw: " + json.dumps(e.extra)[:400])
    except Exception as e:  # noqa: BLE001
        out(f"[FAIL] {name}: {type(e).__name__}: {e}")
    return None


def us_history(broker):
    bars = broker.intraday_bars("SPY", date.today() - timedelta(days=6), date.today())
    if not bars:
        return "NO BARS returned (weekend/holiday range, or history unavailable)"
    last_day = max(b.ts.date() for b in bars)
    reg = [b for b in bars if b.ts.date() == last_day and US.is_regular(b.ts)]
    pre = [b for b in bars if b.ts.date() == last_day and not US.is_regular(b.ts)]
    return (f"{len(bars)} bars, days={sorted({b.ts.date().isoformat() for b in bars})}; {last_day}: {len(reg)} regular "
            f"(first {reg[0].ts.time() if reg else None}, last {reg[-1].ts.time() if reg else None}), {len(pre)} extended-hours")


def us_quote_age(broker):
    q = broker.snapshot(["SPY"])["SPY"]
    age = None if q["ts"] is None else round((datetime.now(US.tz) - q["ts"]).total_seconds(), 1)
    return (f"last={q['last']} quote_time={q['ts']} age={age}s (the engine treats >{US.stale_seconds:.0f}s during the session "
            f"as a stale feed; an old time is normal outside trading hours)")


def history_depth(broker):
    """What the 1-minute history endpoint does, and whether our forward paging now covers a month."""
    from .intraday.data import bars_from_moomoo
    today, lines = date.today(), []

    def raw(start, end):
        return broker._call("GET", "/api/v1.0/quote/US.SPY/history-kline", params={
            "start": start.isoformat(), "end": end.isoformat(), "ktype": 1, "autype": 1, "num": 370, "extended_time": 1})

    def describe(label, d):
        bars = bars_from_moomoo(d.get("kline_list", []), US.tz)
        span = f"{bars[0].ts:%m-%d %H:%M} to {bars[-1].ts:%m-%d %H:%M}" if bars else "empty"
        lines.append(f"{label}: {len(bars)} bars ({span}), next_time={d.get('next_time')}")

    describe(f"raw request start={today - timedelta(days=30)} end={today}", raw(today - timedelta(days=30), today))
    describe("raw request with start == end (expected empty if `end` is exclusive)", raw(today - timedelta(days=3), today - timedelta(days=3)))
    got = broker.intraday_bars("SPY", today - timedelta(days=30), today)
    days = sorted({b.ts.date() for b in got if US.is_regular(b.ts)})
    lines.append(f"our paged fetch of the last 30 days: {len(got)} bars, {len(days)} regular-session days"
                 + (f" ({days[0]} to {days[-1]})" if days else "") + " (about 20 expected for 30 calendar days)")
    return "\n       " + "\n       ".join(lines)


def market_probe(broker, market, symbol):
    """History and quotes are probed separately: they can be refused for different reasons."""
    lines = []
    try:
        bars = broker.intraday_bars(symbol, date.today() - timedelta(days=8), date.today(), market=market)
        seen, want = observed_sessions(bars, market), configured_runs(market)
        verdict = "matches the configured hours" if seen == want else f"DIFFERS from configured {want}: fix data/markets.json"
        lines.append(f"history: {len(bars)} bars over {len({b.ts.date() for b in bars})} days; hours seen {seen} {verdict}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"history: UNAVAILABLE. {explain_error(e, market)}")
    try:
        q = broker.snapshot([symbol], market=market).get(symbol)
        lines.append(f"quote: last={q and q['last']} at {q and q['ts']}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"quote: UNAVAILABLE. {explain_error(e, market)}")
    return f"{market.code} {symbol}\n       " + "\n       ".join(lines)


DEFAULT_PROBES = ["SG.ES3", "SG.D05", "MY.1155", "US.SPY"]


def symbol_lookup(broker, codes):
    """Ask Moomoo which of these codes exist, with name, board lot and exchange (verifies the 100-share lot assumption)."""
    found = {r["code"]: r for r in broker.basic_info(codes)}
    lines = []
    for c in codes:
        r = found.get(c)
        lines.append(f"{c}: " + (f"{r.get('name')} | board lot {r.get('lot_size')} | exchange {r.get('exchange')} | state {r.get('state')}"
                                 if r else "NOT RECOGNISED by Moomoo"))
    return "\n       " + "\n       ".join(lines)


def run_checks(broker, order=False, out=print, probes=None):
    out(f"       account id: {broker.acc_id}")
    step("cash", broker.cash, out)
    step("positions", lambda: [(p.symbol, p.qty, p.avg_price, p.last_price) for p in broker.positions()], out)
    out(f"       positions market filter that worked: {getattr(broker, '_pos_market', 'n/a')}")
    step("last price AAPL", lambda: broker.last_price("AAPL"), out)
    step("last 5 daily closes AAPL", lambda: broker.history("AAPL", 5), out)
    step("SPY quote freshness", lambda: us_quote_age(broker), out)
    step("1-minute SPY history (first regular bar should read 09:30, last 15:59)", lambda: us_history(broker), out)
    step("1-minute history paging (does a month come back?)", lambda: history_depth(broker), out)
    step("Symbol lookup (is the code right, and what is the board lot?)", lambda: symbol_lookup(broker, probes or DEFAULT_PROBES), out)
    step("Singapore (SGX) data", lambda: market_probe(broker, SG, "ES3"), out)
    step("Malaysia (Bursa) data", lambda: market_probe(broker, MY, "1155"), out)
    if order:
        o = step("place 1 share AAPL BUY via the adapter (marketable limit, simulated)",
                 lambda: broker.place_order(Order("AAPL", "BUY", 1)), out)
        if o:
            out(f"       status={o.status} id={o.id} limit_price={o.price} reason={o.reason!r}")
            time.sleep(2)
            step("positions after order", lambda: [(p.symbol, p.qty) for p in broker.positions()], out)


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
    probes = sys.argv[sys.argv.index("--probe") + 1:] if "--probe" in sys.argv else None
    run_checks(broker, order="--order" in sys.argv, probes=[p.upper() for p in probes] if probes else None)
    broker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
