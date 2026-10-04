"""Bar sources: CSV files, Moomoo history-kline rows, and a SYNTHETIC generator for tests/demos only
(results on synthetic data say nothing about real markets)."""
import csv
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from .bars import NY, Bar


def parse_ts(text: str) -> datetime:
    dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=NY) if dt.tzinfo is None else dt.astimezone(NY)


def load_csv(path: str | Path) -> list[Bar]:
    """Columns: timestamp (bar START; tz-aware ISO, or naive = New York time), open, high, low, close, volume."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            out.append(Bar(parse_ts(r.get("timestamp") or r["datetime"]), float(r["open"]), float(r["high"]),
                           float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
    return sorted(out, key=lambda b: b.ts)


def bars_from_moomoo(rows: list[dict]) -> list[Bar]:
    """history-kline rows: time_key is a millisecond epoch. ASSUMPTION: it labels the bar START; verify with
    `python -m bot.smoke_moomoo` (the first regular-session bar of a day should read 09:30)."""
    out = {}
    for k in rows:
        ts = datetime.fromtimestamp(int(k["time_key"]) / 1000, tz=NY)
        out[ts] = Bar(ts, float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]), float(k["volume"]))
    return [out[t] for t in sorted(out)]


def business_days(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic_day(day: date, kind: str = "trend", seed: int = 0, base: float = 100.0) -> list[Bar]:
    """One regular session of 1m bars. kind: 'trend' (up legs with shallow pullbacks) or 'range' (oscillation)."""
    rng = random.Random(f"{seed}-{day}-{kind}")
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    bars, px = [], base
    for m in range(390):
        if kind == "trend":
            if m < 15:
                target = base + 0.15 * math.sin(m / 3)            # tight opening range
            else:
                k = m - 15
                leg = k % 22                                       # 16 min up, 6 min pullback
                drift = 0.08 if leg < 16 else -0.15
                target = px + drift
            vol = 1200 if (m < 15 or (m - 15) % 22 < 16) else 450
        else:
            target = base + 0.30 * math.sin(m / 6.0) + (0.08 * math.sin(m / 1.7))
            vol = 800
        close = target + rng.gauss(0, 0.012)
        high = max(px, close) + abs(rng.gauss(0, 0.015))
        low = min(px, close) - abs(rng.gauss(0, 0.015))
        bars.append(Bar(start + timedelta(minutes=m), px, high, low, close, vol * (0.8 + 0.4 * rng.random())))
        px = close
    return bars


def synthetic_history(days: list[date], kinds: list[str], tickers: list[str], seed: int = 1) -> dict[str, list[Bar]]:
    """Days are chained: each opens at the previous close, like a real market without overnight gaps."""
    out = {}
    for j, t in enumerate(tickers):
        bars, base = [], 100.0 + 40 * j
        for i, d in enumerate(days):
            day_bars = synthetic_day(d, kinds[i % len(kinds)], seed + j, base)
            base = day_bars[-1].close
            bars += day_bars
        out[t] = bars
    return out
