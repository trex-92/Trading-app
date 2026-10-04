from abc import ABC, abstractmethod

from ..models import Order, Position


class Broker(ABC):
    """Minimal broker surface the engine needs. One adapter per vendor."""

    @abstractmethod
    def history(self, symbol: str, bars: int) -> list[float]:
        """Most recent daily closes, oldest first."""

    @abstractmethod
    def last_price(self, symbol: str) -> float: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit; return the order with id/status filled in."""

    def close(self) -> None:
        pass
