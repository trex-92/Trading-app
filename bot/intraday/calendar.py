"""Event-day blocks. Dates are NOT bundled: I could not verify FOMC/CPI/NFP/half-day dates from here, and a wrong
date silently trades a day you meant to skip. Fill data/calendar.json yourself:
  {"fomc_days": ["2026-12-09"], "cpi_nfp_days": ["2026-11-06"], "half_days": ["2026-11-27", "2026-12-24"]}"""
import json
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path


@dataclass
class Calendar:
    fomc_days: set = field(default_factory=set)
    cpi_nfp_days: set = field(default_factory=set)
    half_days: set = field(default_factory=set)
    configured: bool = False

    @classmethod
    def load(cls, path: str | Path) -> "Calendar":
        p = Path(path)
        if not p.is_file():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        parse = lambda xs: {date.fromisoformat(x) for x in xs or []}  # noqa: E731
        return cls(parse(raw.get("fomc_days")), parse(raw.get("cpi_nfp_days")), parse(raw.get("half_days")), True)

    def blocked(self, day: date) -> str | None:
        if day in self.fomc_days:
            return "fomc_day"
        if day in self.half_days:
            return "half_day"
        return None

    def no_entries_before(self, day: date) -> time | None:
        return time(10, 0) if day in self.cpi_nfp_days else None
