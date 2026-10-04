from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Order:
    symbol: str
    side: str  # BUY | SELL
    qty: int
    price: float | None = None  # None = market
    id: str = ""
    status: str = "NEW"
    reason: str = ""
    ts: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    last_price: float = 0.0

    @property
    def pnl(self) -> float:
        return (self.last_price - self.avg_price) * self.qty

    def to_dict(self) -> dict:
        return {**asdict(self), "pnl": round(self.pnl, 2)}
