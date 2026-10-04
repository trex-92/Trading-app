"""Starts one paper engine per market the user switched on in the app, and applies the on/off switch and the symbol list.

 * A market's runner is created the first time it is enabled and then lives until the bot stops. Switching it off in the
   app only stops NEW entries; an open position is still managed to its exit.
 * Symbol changes take effect at the start of the next trading day for that market.
 * US trades on the Moomoo simulated account. SG and MY have no Moomoo simulator, so they run on the bot's own simulated
   account (real quotes, simulated fills): see bot/brokers/local_paper.py.
"""
import threading

from .market import DEFAULTS, get_market


class MarketSupervisor:
    def __init__(self, sync, make_runner, log=print, markets_path="data/markets.json"):
        self.sync, self.make_runner, self.log, self.markets_path = sync, make_runner, log, markets_path
        self.runners: dict = {}
        self.symbols: dict[str, list[str]] = {}
        self.threads: list[threading.Thread] = []
        self.stop = threading.Event()
        self._warned = False

    def symbols_for(self, code: str) -> list[str]:
        return self.symbols.get(code) or list(get_market(code, self.markets_path).default_symbols)

    def poll_once(self) -> None:
        rows = {r["market"]: r for r in self.sync.get_market_configs() if r.get("market") in DEFAULTS}
        if not any(r.get("enabled") for r in rows.values()) and not self.runners and not self._warned:
            self._warned = True
            self.log("[markets] no market is switched on yet: open the app, Settings, Markets")
        for code, row in rows.items():
            self.symbols[code] = [s.strip().upper() for s in (row.get("symbols") or []) if s.strip()]
            if code not in self.runners and row.get("enabled"):
                try:
                    runner = self.make_runner(code, row, lambda c=code: self.symbols_for(c))
                except Exception as e:  # noqa: BLE001
                    self.log(f"[markets] cannot start {code}: {e}")
                    continue
                self.runners[code] = runner
                t = threading.Thread(target=runner.run_forever, args=(self.stop,), daemon=True, name=f"engine-{code}")
                t.start()
                self.threads.append(t)
                self.log(f"[markets] {code} engine started (symbols {self.symbols_for(code)})")
            if code in self.runners:
                if self.runners[code].enabled != bool(row.get("enabled")):
                    self.log(f"[markets] {code} {'switched on' if row.get('enabled') else 'switched off (open positions still managed)'}")
                self.runners[code].enabled = bool(row.get("enabled"))

    def run_forever(self, stop: threading.Event, interval: float = 30.0) -> None:
        self.stop = stop
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001
                self.log(f"[markets] poll error: {e}")
            stop.wait(interval)
