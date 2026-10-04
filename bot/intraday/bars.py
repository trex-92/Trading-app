"""Bar type. Session logic lives in market.py; all timestamps are tz-aware (the market's own zone, so DST is handled
by the tz database and nothing hard-codes UTC offsets)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")  # kept for the US profile and existing callers
ONE_MIN = timedelta(minutes=1)
FIVE_MIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Bar:
    ts: datetime  # START of the bar (tz-aware)
    open: float
    high: float
    low: float
    close: float
    volume: float
