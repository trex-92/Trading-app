import itertools
import random

from ..models import Order, Position
from .base import Broker


class PaperBroker(Broker):
    """In-memory broker with a random-walk price feed. For tests and dry runs."""

    def __init__(self, cash: float = 100_000, seed: int | None = None):
        self._cash = cash
        self._pos: dict[str, Position] = {}
        self._px: dict[str, list[float]] = {}
        self._rng = random.Random(seed)
        self._ids = itertools.count(1)

    def _series(self, symbol: str) -> list[float]:
        s = self._px.setdefault(symbol, [100.0])
        s.append(max(1.0, s[-1] * (1 + self._rng.gauss(0, 0.01))))
        return s

    def history(self, symbol, bars):
        s = self._series(symbol)  # each call simulates a new bar
        while len(s) < bars:
            self._series(symbol)
        return s[-bars:]

    def last_price(self, symbol):
        return self._series(symbol)[-1]

    def positions(self):
        for p in self._pos.values():
            p.last_price = self._px[p.symbol][-1]
        return [p for p in self._pos.values() if p.qty]

    def cash(self):
        return self._cash

    def place_order(self, order):
        px = order.price or self.last_price(order.symbol)
        sign = 1 if order.side == "BUY" else -1
        pos = self._pos.get(order.symbol) or Position(order.symbol, 0, 0.0)
        if sign > 0:
            total = pos.avg_price * pos.qty + px * order.qty
            pos.qty += order.qty
            pos.avg_price = total / pos.qty
        else:
            if order.qty > pos.qty:
                order.status, order.id = "REJECTED", f"paper-{next(self._ids)}"
                return order
            pos.qty -= order.qty
        self._cash -= sign * px * order.qty
        pos.last_price = px
        self._pos[order.symbol] = pos
        order.id, order.status, order.price = f"paper-{next(self._ids)}", "FILLED", px
        return order
