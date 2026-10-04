import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(*paths: Path) -> None:
    """Minimal .env loader (KEY=VALUE, '#' comments). Real environment variables win over the file."""
    for path in paths:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:  # a blank value means "use the default", not an empty string
                os.environ.setdefault(key.strip(), value)


load_dotenv(Path.cwd() / "bot" / ".env", Path(__file__).with_name(".env"))


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
    run_sma_demo: bool = field(default_factory=lambda: os.getenv("RUN_SMA_DEMO", "yes") == "yes")
    command_poll_seconds: int = field(default_factory=lambda: int(os.getenv("COMMAND_POLL_SECONDS", "3")))
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    supabase_user_id: str = field(default_factory=lambda: os.getenv("SUPABASE_USER_ID", ""))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "trading.db"))

    def validate(self) -> None:
        if self.trade_env == "REAL" and not self.allow_live:
            raise SystemExit("TRADE_ENV=REAL requires ALLOW_LIVE=yes")
        if self.sma_fast >= self.sma_slow:
            raise SystemExit("SMA_FAST must be < SMA_SLOW")
        if not (self.supabase_url and self.supabase_service_key and self.supabase_user_id):
            raise SystemExit("Set SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_USER_ID")
        try:
            uuid.UUID(self.supabase_user_id)
        except ValueError:
            raise SystemExit(
                f"SUPABASE_USER_ID must be the user's UUID (Supabase dashboard > Authentication > Users > ID "
                f"column, looks like 123e4567-e89b-12d3-a456-426614174000), got {self.supabase_user_id!r}") from None
