"""Bar type and New York session helpers. All timestamps are tz-aware America/New_York, so US
daylight-saving shifts are handled by the tz database and nothing here hard-codes UTC offsets."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception as e:  # pragma: no cover - Windows without the tz database
    raise RuntimeError("Time zone database missing. Run: python -m pip install tzdata") from e

OPEN = time(9, 30)
CLOSE = time(16, 0)
ONE_MIN = timedelta(minutes=1)
FIVE_MIN = timedelta(minutes=5)


@dataclass(frozen=True)
class Bar:
    ts: datetime  # START of the bar (tz-aware, New York)
    open: float
    high: float
    low: float
    close: float
    volume: float


def tod(ts: datetime) -> time:
    return time(ts.hour, ts.minute, ts.second)


def minute_of_session(ts: datetime) -> int:
    return ts.hour * 60 + ts.minute - (9 * 60 + 30)


def is_regular(ts: datetime) -> bool:
    return OPEN <= tod(ts) < CLOSE


def is_premarket(ts: datetime) -> bool:
    return time(4, 0) <= tod(ts) < OPEN
