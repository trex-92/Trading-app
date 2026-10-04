import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in os.getenv(name, default).split(",") if s.strip()]


@dataclass
class Config:
    broker: str = field(default_factory=lambda: os.getenv("BROKER", "paper").lower())
    allow_live: bool = field(default_factory=lambda: os.getenv("ALLOW_LIVE", "no") == "yes")
    trade_env: str = field(default_factory=lambda: os.getenv("TRADE_ENV", "SIMULATE").upper())
    symbols: list[str] = field(default_factory=lambda: _list("SYMBOLS", "AAPL"))
    sma_fast: int = field(default_factory=lambda: int(os.getenv("SMA_FAST", "10")))
    sma_slow: int = field(default_factory=lambda: int(os.getenv("SMA_SLOW", "30")))
    order_qty: int = field(default_factory=lambda: int(os.getenv("ORDER_QTY", "1")))
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "60")))
    max_position_qty: int = field(default_factory=lambda: int(os.getenv("MAX_POSITION_QTY", "10")))
    max_orders_per_day: int = field(default_factory=lambda: int(os.getenv("MAX_ORDERS_PER_DAY", "20")))
    max_daily_loss: float = field(default_factory=lambda: float(os.getenv("MAX_DAILY_LOSS", "200")))
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))
    api_host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "trading.db"))

    def validate(self) -> None:
        if self.trade_env == "REAL" and not self.allow_live:
            raise SystemExit("TRADE_ENV=REAL requires ALLOW_LIVE=yes")
        if self.sma_fast >= self.sma_slow:
            raise SystemExit("SMA_FAST must be < SMA_SLOW")
        if not self.api_token or self.api_token == "change-me":
            raise SystemExit("Set a real API_TOKEN")
