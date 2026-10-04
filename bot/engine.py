import threading
import time

from .brokers.base import Broker
from .config import Config
from .models import Order
from .risk import RiskManager
from .store import Store
from .strategy.base import Strategy


class Engine:
    def __init__(self, cfg: Config, broker: Broker, strategy: Strategy, store: Store, risk: RiskManager):
        self.cfg, self.broker, self.strategy, self.store, self.risk = cfg, broker, strategy, store, risk
        self._stop = threading.Event()
        self.start_equity: float | None = None

    def equity(self) -> float:
        return self.broker.cash() + sum(p.last_price * p.qty for p in self.broker.positions())

    def day_pnl(self) -> float:
        return 0.0 if self.start_equity is None else self.equity() - self.start_equity

    def tick(self) -> None:
        """One pass over all symbols. Exceptions per symbol are logged, never fatal."""
        if self.start_equity is None:
            self.start_equity = self.equity()
        held = {p.symbol: p.qty for p in self.broker.positions()}
        for sym in self.cfg.symbols:
            try:
                closes = self.broker.history(sym, self.strategy.warmup + 1)
                side = self.strategy.signal(closes, held.get(sym, 0))
                if not side:
                    continue
                order = Order(sym, side, self.cfg.order_qty, reason=f"{type(self.strategy).__name__} signal")
                why = self.risk.check(order, held.get(sym, 0), self.store.orders_today(), self.day_pnl())
                if why:
                    order.status, order.reason = "BLOCKED", why
                    self.store.log("WARN", f"{sym} {side} blocked: {why}")
                else:
                    order = self.broker.place_order(order)
                    self.store.log("INFO", f"{sym} {side} x{order.qty} -> {order.status}")
                self.store.add_order(order)
            except Exception as e:  # noqa: BLE001
                self.store.log("ERROR", f"{sym}: {e}")
        snap = self.snapshot()
        self.store.set("snapshot", snap)
        if self.store.sync:
            self.store.sync.push_snapshot(snap)

    def apply_commands(self) -> None:
        sync = self.store.sync
        if not sync:
            return
        for cmd in sync.claim_commands():
            self.risk.halted = cmd == "halt"
            self.store.log("WARN" if self.risk.halted else "INFO", f"trading {cmd.upper()} from app")
            sync.push_snapshot(self.snapshot())

    def snapshot(self) -> dict:
        return {
            "equity": round(self.equity(), 2),
            "cash": round(self.broker.cash(), 2),
            "day_pnl": round(self.day_pnl(), 2),
            "halted": self.risk.halted,
            "positions": [p.to_dict() for p in self.broker.positions()],
            "broker": self.cfg.broker,
            "env": self.cfg.trade_env,
        }

    def run(self) -> None:
        self.store.log("INFO", f"engine started broker={self.cfg.broker} env={self.cfg.trade_env}")
        next_tick = 0.0
        while not self._stop.is_set():
            self.apply_commands()
            if time.monotonic() >= next_tick:
                self.tick()
                next_tick = time.monotonic() + self.cfg.poll_seconds
            self._stop.wait(self.cfg.command_poll_seconds)

    def stop(self) -> None:
        self._stop.set()
