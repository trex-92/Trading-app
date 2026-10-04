"""Bar sources: CSV files, Moomoo history-kline rows, and a SYNTHETIC generator for tests/demos only
(results on synthetic data say nothing about real markets)."""
import csv
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from .bars import NY, Bar
from .market import US, Market


def parse_ts(text: str, tz: ZoneInfo = NY) -> datetime:
    dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def load_csv(path: str | Path, tz: ZoneInfo = NY) -> list[Bar]:
    """Columns: timestamp (bar START; tz-aware ISO, or naive = the market's local time), open, high, low, close, volume."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            out.append(Bar(parse_ts(r.get("timestamp") or r["datetime"], tz), float(r["open"]), float(r["high"]),
                           float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
    return sorted(out, key=lambda b: b.ts)


def bars_from_moomoo(rows: list[dict], tz: ZoneInfo = NY) -> list[Bar]:
    """history-kline rows: time_key is a millisecond epoch. ASSUMPTION: it labels the bar START; verify with
    `python -m bot.smoke_moomoo` (the first regular-session bar of a day should read 09:30)."""
    out = {}
    for k in rows:
        ts = datetime.fromtimestamp(int(k["time_key"]) / 1000, tz=tz)
        out[ts] = Bar(ts, float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]), float(k["volume"]))
    return [out[t] for t in sorted(out)]


def business_days(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_day(day: date, kind: str = "trend", seed: int = 0, base: float = 100.0, market: Market = US) -> list[Bar]:
    """One regular session of 1m bars for `market`. kind: 'trend' (up legs with pullbacks) or 'range' (oscillation)."""
    rng = random.Random(f"{seed}-{day}-{kind}-{market.code}")
    bars, px = [], base
    for m in range(market.total_minutes):
        if kind == "trend":
            if m < 15:
                target = base + 0.15 * math.sin(m / 3) * base / 100   # tight opening range
            else:
                k = m - 15
                leg = k % 22                                         # 16 min up, 6 min pullback
                drift = (0.08 if leg < 16 else -0.15) * base / 100
                target = px + drift
            vol = 1200 if (m < 15 or (m - 15) % 22 < 16) else 450
        else:
            target = base + (0.30 * math.sin(m / 6.0) + 0.08 * math.sin(m / 1.7)) * base / 100
            vol = 800
        noise = base / 100
        close = target + rng.gauss(0, 0.012 * noise)
        high = max(px, close) + abs(rng.gauss(0, 0.015 * noise))
        low = min(px, close) - abs(rng.gauss(0, 0.015 * noise))
        bars.append(Bar(market.ts_at(day, m), px, high, low, close, vol * (0.8 + 0.4 * rng.random())))
        px = close
    return bars


def synthetic_history(days: list[date], kinds: list[str], tickers: list[str], seed: int = 1,
                      market: Market = US) -> dict[str, list[Bar]]:
    """Days are chained: each opens at the previous close, like a real market without overnight gaps."""
    out = {}
    for j, t in enumerate(tickers):
        bars, base = [], 100.0 + 40 * j
        for i, d in enumerate(days):
            day_bars = synthetic_day(d, kinds[i % len(kinds)], seed + j, base, market)
            base = day_bars[-1].close
            bars += day_bars
        out[t] = bars
    return out
