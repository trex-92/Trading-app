from abc import ABC, abstractmethod


class Strategy(ABC):
    warmup: int = 1

    @abstractmethod
    def signal(self, closes: list[float], held_qty: int) -> str | None:
        """Return 'BUY', 'SELL' or None."""
