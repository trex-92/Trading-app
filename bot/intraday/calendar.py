"""Event-day blocks, per market. Dates are NOT bundled: I could not verify FOMC/CPI/NFP/holiday/half-day dates from here,
and a wrong date silently trades a day you meant to skip. Fill data/calendar.json yourself. Two layouts are accepted:

  {"fomc_days": [...], "cpi_nfp_days": [...], "half_days": [...], "blocked_days": [...]}          # US only (original)
  {"markets": {"US": {...}, "SG": {"half_days": [...], "blocked_days": [...]}, "MY": {...}}}   # per market

fomc_days / half_days / blocked_days: no trading at all. cpi_nfp_days: no entries in the first 30 trading minutes.
Exchange holidays need no entry: a day with no bars is simply not traded."""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

NO_ENTRY_MINUTES = 30  # US: before 10:00


@dataclass
class Calendar:
    fomc_days: set = field(default_factory=set)
    cpi_nfp_days: set = field(default_factory=set)
    half_days: set = field(default_factory=set)
    blocked_days: set = field(default_factory=set)
    configured: bool = False

    @classmethod
    def load(cls, path: str | Path, market: str = "US") -> "Calendar":
        p = Path(path)
        if not p.is_file():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        if "markets" in raw:
            raw = raw["markets"].get(market)
        elif market != "US":
            raw = None  # the flat layout means US only
        if raw is None:
            return cls()
        parse = lambda xs: {date.fromisoformat(x) for x in xs or []}  # noqa: E731
        return cls(parse(raw.get("fomc_days")), parse(raw.get("cpi_nfp_days")), parse(raw.get("half_days")),
                   parse(raw.get("blocked_days")), True)

    def blocked(self, day: date) -> str | None:
        if day in self.fomc_days:
            return "fomc_day"
        if day in self.half_days:
            return "half_day"
        if day in self.blocked_days:
            return "blocked_day"
        return None

    def no_entries_before(self, day: date) -> int | None:
        """Trading minutes elapsed before which no entries may be taken today (None = no restriction)."""
        return NO_ENTRY_MINUTES if day in self.cpi_nfp_days else None
