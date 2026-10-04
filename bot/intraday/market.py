"""Market profiles: trading hours, time zone, currency, lot size and cost assumptions.

Everything session-related in the strategies is expressed in TRADING MINUTES since the open (so a lunch break does not
distort windows, time stops or 5-minute bars). The defaults below are my best knowledge and are NOT verified from here:
check them with `python -m bot.smoke_moomoo` (it prints the hours actually seen in the data) and override anything in
`data/markets.json`, e.g. {"SG": {"sessions": [["09:00","12:00"],["13:00","17:00"]], "cost_pct_round_trip": 0.25}}.
Cost numbers are PLACEHOLDERS: replace them with your real Moomoo fee schedule (brokerage, clearing, stamp duty, GST).
"""
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    tz: ZoneInfo
    sessions: tuple          # ((open, close), ...) in local time; a gap between them is a lunch break
    prefix: str              # Moomoo symbol prefix
    currency: str
    lot_size: int
    default_symbols: tuple
    ticker_pattern: str
    extended_hours: bool = False          # pre/after-market bars exist and are used for premarket levels
    sim_account: bool = False             # Moomoo offers a simulated trading account for this market
    cost_pct_round_trip: float = 0.0      # PLACEHOLDER: % of notional, both sides, all fees
    cost_per_share_round_trip: float = 0.0
    stale_seconds: float = 10.0           # quote older than this = feed stale; thinly traded markets need longer

    # ---- session arithmetic --------------------------------------------------------------------------
    @property
    def total_minutes(self) -> int:
        return sum(_minutes(b) - _minutes(a) for a, b in self.sessions)

    def minute_of_session(self, ts: datetime) -> int | None:
        """Trading minutes elapsed since the open at the START of the bar `ts`; None outside trading hours."""
        t = ts.astimezone(self.tz).time()
        done = 0
        for a, b in self.sessions:
            if a <= t < b:
                return done + _minutes(t) - _minutes(a)
            done += _minutes(b) - _minutes(a)
        return None

    def is_regular(self, ts: datetime) -> bool:
        return self.minute_of_session(ts) is not None

    def is_pre(self, ts: datetime) -> bool:
        if not self.extended_hours:
            return False
        t = ts.astimezone(self.tz).time()
        return time(4, 0) <= t < self.sessions[0][0]

    def is_trading_weekday(self, ts_or_day) -> bool:
        return ts_or_day.weekday() < 5

    def ts_at(self, day: date, minute: int) -> datetime:
        """Wall-clock START time of trading minute `minute` on `day`."""
        for a, b in self.sessions:
            length = _minutes(b) - _minutes(a)
            if minute < length:
                return datetime.combine(day, a, tzinfo=self.tz) + timedelta(minutes=minute)
            minute -= length
        raise ValueError("minute beyond the session")

    def now(self) -> datetime:
        return datetime.now(self.tz)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _t(s: str) -> time:
    return time.fromisoformat(s)


US = Market("US", "United States", ZoneInfo("America/New_York"), ((time(9, 30), time(16, 0)),), "US.", "USD", 1,
            ("SPY", "QQQ"), r"^[A-Z][A-Z.]{0,5}$", extended_hours=True, sim_account=True,
            cost_per_share_round_trip=0.02, stale_seconds=10.0)
SG = Market("SG", "Singapore (SGX)", ZoneInfo("Asia/Singapore"), ((time(9, 0), time(12, 0)), (time(13, 0), time(17, 0))),
            "SG.", "SGD", 100, ("ES3",), r"^[A-Z0-9][A-Z0-9.]{0,9}$", cost_pct_round_trip=0.15, stale_seconds=60.0)
MY = Market("MY", "Malaysia (Bursa)", ZoneInfo("Asia/Kuala_Lumpur"), ((time(9, 0), time(12, 30)), (time(14, 30), time(17, 0))),
            "MY.", "MYR", 100, ("1155",), r"^[A-Z0-9][A-Z0-9.]{0,9}$", cost_pct_round_trip=0.30, stale_seconds=60.0)

DEFAULTS = {m.code: m for m in (US, SG, MY)}


def get_market(code: str, overrides_path: str | Path | None = "data/markets.json") -> Market:
    code = code.upper()
    if code not in DEFAULTS:
        raise ValueError(f"unknown market {code!r}; choose one of {sorted(DEFAULTS)}")
    m = DEFAULTS[code]
    if overrides_path and Path(overrides_path).is_file():
        raw = json.loads(Path(overrides_path).read_text(encoding="utf-8-sig")).get(code, {})
        fields = {}
        for k, v in raw.items():
            if k == "sessions":
                fields[k] = tuple((_t(a), _t(b)) for a, b in v)
            elif k == "tz":
                fields[k] = ZoneInfo(v)
            elif k in ("lot_size",):
                fields[k] = int(v)
            elif k in ("cost_pct_round_trip", "cost_per_share_round_trip", "stale_seconds"):
                fields[k] = float(v)
            elif k in ("default_symbols",):
                fields[k] = tuple(v)
            else:
                raise ValueError(f"data/markets.json: cannot override {k!r}")
        m = replace(m, **fields)
    for a, b in m.sessions:
        if (_minutes(b) - _minutes(a)) % 5:
            raise ValueError("each session segment must be a whole number of 5-minute bars")
    return m


def observed_sessions(bars, market: Market) -> list[tuple[str, str]]:
    """Contiguous runs of 1-minute bars on the busiest recent day, e.g. [("09:00", "12:29"), ("14:30", "16:59")].
    Used by the smoke test to compare the hours configured above with the hours actually present in the data."""
    by_day: dict = {}
    for b in bars:
        t = b.ts.astimezone(market.tz)
        by_day.setdefault(t.date(), []).append(t)
    if not by_day:
        return []
    day = max(by_day, key=lambda d: len(by_day[d]))
    runs, start, prev = [], None, None
    for t in sorted(by_day[day]):
        if prev is None or (t - prev).total_seconds() > 60:
            if start is not None:
                runs.append((start.strftime("%H:%M"), prev.strftime("%H:%M")))
            start = t
        prev = t
    runs.append((start.strftime("%H:%M"), prev.strftime("%H:%M")))
    return runs


def configured_runs(market: Market) -> list[tuple[str, str]]:
    """Same format as observed_sessions: the last whole minute of each configured segment."""
    out = []
    for a, b in market.sessions:
        last = (datetime.combine(date(2000, 1, 1), b) - timedelta(minutes=1)).time()
        out.append((a.strftime("%H:%M"), last.strftime("%H:%M")))
    return out


def explain_error(err: Exception, market: Market) -> str:
    """Turn the two known Moomoo data refusals into advice; anything else passes through unchanged."""
    text = str(err)
    low = text.lower()
    if "permission" in low or "quote right" in low:
        return (f"Your Moomoo account has no real-time {market.name} quote right ({text}). Enable or buy the {market.name} "
                f"quote subscription in the Moomoo app, then try again.")
    if "rate limit" in low:
        return ("Moomoo is limiting how fast history can be downloaded. Wait a minute and run the backtest again: every day already "
                "downloaded is saved on the bot PC (data/cache), so each retry gets further and a rerun is quick.")
    if "invalid symbol" in low or "invalid_symbol" in low:
        return (f"Moomoo does not recognise that {market.name} symbol ({text}). Use the exact code the Moomoo app shows under "
                f"the stock name, with the '{market.prefix}' prefix, and check it with: python -m bot.smoke_moomoo --probe {market.prefix}<code>")
    if "unsupported market" in low:
        return (f"Moomoo's API does not serve {market.name} price data for this account ({market.prefix} symbols are 'unsupported'). "
                f"Backtest with your own 1-minute CSV files: python -m bot.intraday.cli --market {market.code} --csv-dir data/{market.code.lower()}")
    return text
