"""Moomoo REST adapter (no OpenD). https://open.moomoo.com/api/overview/getting-started

Two backends share the quote endpoints:
  SIMULATE -> /api/v1.0/sim-trade/...   (envelope {ret_code, data})
  REAL     -> /api/v1.0/accounts/...    (envelope {s: ok|error, d})
REAL orders that need confirmation (errcode -2100) or a browser step (-2101) are NOT auto-confirmed:
the order is marked REJECTED with the confirm_id in its reason so a human decides.

Unverified against the live service (docs only): whether sim-trade endpoints accept the Bearer token,
and Bearer scopes needed (trade:read/trade:write/quote:read).
"""
import json
import os
import threading
import time
from pathlib import Path
from datetime import date, datetime, timedelta

import httpx

from ..intraday.bars import NY
from ..intraday.market import US, Market
from ..intraday.qty import clean, fmt as fmt_qty
from ..models import Order, Position
from .base import Broker
from ..moomoo_oauth import BASE, AuthError, OAuthSession, TokenStore

US_MARKET_ID = 2
# Simulated US account as actually returned by the service: "美股融资融券模拟账户" with market_id 100
# (the docs list 2). Override the market sent with orders via MOOMOO_SIM_MARKET if moomoo rejects it.
US_SIM_MARKET_IDS = (2, 100)


class MoomooError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None, extra: dict | None = None):
        super().__init__(msg)
        self.code, self.extra = code, extra or {}


class MoomooRestBroker(Broker):
    def __init__(self, trade_env: str = "SIMULATE", acc_id: str = "", session: OAuthSession | None = None,
                 http: httpx.Client | None = None, sleep=time.sleep, cache_dir: str | None = None, now=datetime.now,
                 history_limit: tuple[int, float] = (12, 30.0), clock=time.monotonic):
        self.real = trade_env == "REAL"
        self.http = http or httpx.Client(base_url=BASE, timeout=15)
        self.auth = session or OAuthSession(TokenStore())
        self._sleep = sleep
        self._now = now   # callable(tz) -> aware datetime; injectable for tests
        self._clock = clock
        self._hist_calls: list[float] = []   # times of recent history requests (the sliding window)
        self._hist_limit = history_limit      # (max requests, per seconds). Moomoo publishes no number; this is deliberately cautious
        self._hist_lock = threading.Lock()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.sim_market = int(os.getenv("MOOMOO_SIM_MARKET") or 0) or US_MARKET_ID
        self.acc_id = acc_id or self._discover_account()

    # ---- plumbing -------------------------------------------------------------------------
    RATE_LIMIT_WAITS = (2, 4, 8, 16, 32)   # seconds; Moomoo publishes no numbers, only "back off exponentially"

    def _call(self, method: str, path: str, **kw):
        """Like _request, but a 'rate limit exceeded' answer (which arrives as a normal reply, not HTTP 429) is retried
        with exponential backoff before giving up."""
        for wait in (*self.RATE_LIMIT_WAITS, None):
            try:
                return self._request(method, path, **kw)
            except MoomooError as e:
                if "rate limit" not in str(e).lower() or wait is None:
                    raise
                self._sleep(wait)

    def _throttle_history(self) -> None:
        """History pages are the heaviest calls and the ones that hit the rate limit. Keep them under a sliding-window cap
        so a month-long backtest takes a couple of minutes instead of failing. Quotes and orders are not delayed by this."""
        n, window = self._hist_limit
        with self._hist_lock:
            now = self._clock()
            self._hist_calls = [t for t in self._hist_calls if now - t < window]
            if len(self._hist_calls) >= n:
                wait = window - (now - self._hist_calls[0])
                if wait > 0:
                    self._sleep(wait)
                now = self._clock()
                self._hist_calls = [t for t in self._hist_calls if now - t < window]
            self._hist_calls.append(now)

    def _request(self, method: str, path: str, **kw):
        """HTTP with bearer auth, one refresh-and-retry on 401, backoff on 429. Returns unwrapped payload."""
        if path.endswith("/history-kline"):
            self._throttle_history()
        force = False
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {self.auth.access_token(force_refresh=force)}"}
            r = self.http.request(method, path, headers=headers, **kw)
            if r.status_code == 401 and not force:
                force = True
                continue
            if r.status_code == 429:
                self._sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            break
        else:
            raise MoomooError(f"{method} {path}: gave up after retries")
        if r.status_code == 401:
            raise AuthError("Unauthorized even after token refresh; run: python -m bot.moomoo_login")
        try:
            body = r.json()
        except ValueError:
            raise MoomooError(f"{method} {path}: HTTP {r.status_code} non-JSON response") from None
        if "s" in body:  # trading envelope
            if body["s"] != "ok":
                raise MoomooError(body.get("errmsg", "error"), body.get("errcode"), body)
            return body.get("d")
        if body.get("ret_code", -1) != 0:  # quote / sim-trade envelope
            raise MoomooError(body.get("ret_msg", "error"), body.get("ret_code"), body)
        return body.get("data")

    def _discover_account(self) -> str:
        if self.real:
            accts = (self._call("GET", "/api/v1.0/accounts/authorized_trd_accs") or {}).get("accounts", [])
            if len(accts) != 1:
                raise MoomooError(f"Found {len(accts)} authorized accounts; set MOOMOO_ACC_ID explicitly "
                                  f"(ids: {[a['account_id'] for a in accts]})")
            return str(accts[0]["account_id"])
        accts = (self._call("GET", "/api/v1.0/sim-trade/accounts") or {}).get("accounts", [])
        us = [a for a in accts if a.get("market_id") in US_SIM_MARKET_IDS]
        if us and not os.getenv("MOOMOO_SIM_MARKET"):
            self.sim_market = int(us[0]["market_id"])
        if not us:
            raise MoomooError(f"No US simulated account found; accounts returned: {accts}")
        return str(us[0]["account_id"])

    @staticmethod
    def _code(symbol: str, market: Market = US) -> str:
        return symbol if "." in symbol else f"{market.prefix}{symbol}"

    # ---- Broker interface -----------------------------------------------------------------
    def history(self, symbol, bars):
        data = self._call("GET", f"/api/v1.0/quote/{self._code(symbol)}/history-kline",
                          params={"end": date.today().isoformat(), "ktype": 2, "autype": 1, "num": min(bars + 5, 370)})
        rows = sorted(data.get("kline_list", []), key=lambda k: k["time_key"])
        return [float(k["close"]) for k in rows][-bars:]

    # Observed behaviour of /quote/{code}/history-kline (your account, Oct 2026): with `start` given it returns the FIRST
    # ~1000 bars at/after `start` in ascending order (a full US day with extended hours is ~960 bars), says next_time=None
    # even when truncated, and treats `end` as exclusive (start == end returns nothing). So history is read forward by
    # moving `start` to the date of the last bar received; the date always advances because a day has fewer than 1000 bars.
    HISTORY_PAGE_CAP = 1000

    # ---- completed days are cached on disk: each page is ~1 day and the API rate-limits, so never refetch a finished day ----
    def _cache_file(self, market: Market, symbol: str, day: date):
        return self.cache_dir / f"{market.code}_{symbol}_{day.isoformat()}.json"

    def _cache_read(self, market, symbol, day):
        try:
            return json.loads(self._cache_file(market, symbol, day).read_text())
        except (FileNotFoundError, ValueError, TypeError):
            return None

    def _cache_write(self, market, symbol, day, rows):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file(market, symbol, day).with_suffix(".tmp")
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, self._cache_file(market, symbol, day))

    def intraday_bars(self, symbol: str, start: date, end: date, market: Market = US, ktype: int = 1, max_pages: int = 150):
        from ..intraday.data import bars_from_moomoo
        today = self._now(market.tz).date()
        weekdays = [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5]
        use_cache = bool(self.cache_dir) and ktype == 1
        rows, missing = [], []
        for d in weekdays:
            cached = self._cache_read(market, symbol, d) if (use_cache and d < today) else None
            if cached is None:
                missing.append(d)
            else:
                rows += cached   # an empty list is a confirmed no-data day (holiday)
        if missing:
            def store(by_day, upto):
                """Called after every page: finished days are saved at once, so a failure later does not lose them."""
                if use_cache:
                    for d in missing:
                        if d < today and d <= upto and not self._cache_file(market, symbol, d).exists():
                            self._cache_write(market, symbol, d, by_day.get(d, []))
            rows += self._fetch_forward(symbol, min(missing), end, market, ktype, max_pages, store)
        seen, unique = set(), []
        for k in rows:
            if k["time_key"] not in seen:
                seen.add(k["time_key"])
                unique.append(k)
        return [b for b in bars_from_moomoo(unique, market.tz) if start <= b.ts.astimezone(market.tz).date() <= end]

    def _fetch_forward(self, symbol, start, end, market, ktype, max_pages, store):
        """Read forward from `start`; after each page report which days are certainly complete (the last day of a truncated
        page may be partial, so it is excluded until the next page)."""
        rows, seen, by_day, cursor = [], set(), {}, start
        for _ in range(max_pages):
            params = {"start": cursor.isoformat(), "end": (end + timedelta(days=1)).isoformat(), "ktype": ktype,
                      "autype": 1, "num": 370}
            if market.extended_hours:
                params["extended_time"] = 1   # pre/after-market bars (US only)
            try:
                data = self._call("GET", f"/api/v1.0/quote/{self._code(symbol, market)}/history-kline", params=params)
            except MoomooError as e:
                if "invalid" in str(e).lower() and symbol not in str(e):
                    raise MoomooError(f"{symbol}: {e}", e.code, e.extra) from e   # say WHICH symbol
                raise
            page = data.get("kline_list", [])
            new = [k for k in page if k["time_key"] not in seen]
            seen.update(k["time_key"] for k in new)
            rows += new
            for k in new:
                by_day.setdefault(datetime.fromtimestamp(k["time_key"] / 1000, tz=market.tz).date(), []).append(k)
            if not new or len(page) < self.HISTORY_PAGE_CAP * 0.9:
                store(by_day, end)          # short page: the data ended, everything up to `end` is settled
                return rows
            last_day = datetime.fromtimestamp(max(k["time_key"] for k in page) / 1000, tz=market.tz).date()
            store(by_day, last_day - timedelta(days=1))
            cursor = max(last_day, cursor + timedelta(days=1))   # always move forward
            if cursor > end:
                if len(page) < self.HISTORY_PAGE_CAP:   # not cut off by the page cap: every day up to `end` is complete, save it
                    store(by_day, end)
                return rows
        return rows

    def basic_info(self, codes: list[str]) -> list[dict]:
        """Static facts (name, board lot, exchange, state) for full codes like 'MY.1155'. Unknown codes are simply absent."""
        data = self._call("POST", "/api/v1.0/quote/stock-basicinfo", json={"code_list": codes})
        return data.get("basic_list", [])

    def unknown_symbols(self, symbols: list[str], market: Market = US) -> list[str]:
        """The subset of `symbols` that Moomoo does not recognise (one request for the whole list)."""
        codes = {self._code(x, market): x for x in symbols}
        known = {r["code"] for r in self.basic_info(list(codes))}
        return [x for code, x in codes.items() if code not in known]

    # ---- live paper-trading support (simulated account only; REAL is deliberately not implemented) ------
    SIM_STATUS = {2: "OPEN", 3: "PARTIAL", 4: "FILLED", 5: "CANCELLED", 6: "REJECTED"}

    def _sim_only(self, what: str):
        if self.real:
            raise NotImplementedError(f"{what} is only implemented for the simulated account (paper engine)")

    def snapshot(self, symbols: list[str], market: Market = US) -> dict:
        """{symbol: {last, bid, ask, ts}} where ts is the quote's update time (None if the service gave none)."""
        data = self._call("POST", "/api/v1.0/quote/snapshot", json={"code_list": [self._code(x, market) for x in symbols]})
        out = {}
        for r in data.get("snapshot_list", []):
            ut = int(r.get("update_time") or 0)
            out[r["code"].split(".", 1)[-1]] = {
                "last": float(r["last_price"]), "bid": float(r.get("bid_price") or 0), "ask": float(r.get("ask_price") or 0),
                "ts": datetime.fromtimestamp(ut / 1000, tz=market.tz) if ut > 0 else None}
        return out

    def recent_bars(self, symbol: str, n: int = 15, market: Market = US):
        """The newest n bars of TODAY. Reads forward from today's date (a day has fewer than 1000 bars, so it is complete)."""
        from ..intraday.data import bars_from_moomoo
        today = self._now(market.tz).date()
        params = {"start": today.isoformat(), "end": (today + timedelta(days=1)).isoformat(), "ktype": 1, "autype": 1, "num": 370}
        if market.extended_hours:
            params["extended_time"] = 1
        data = self._call("GET", f"/api/v1.0/quote/{self._code(symbol, market)}/history-kline", params=params)
        return bars_from_moomoo(data.get("kline_list", []), market.tz)[-n:]

    def equity(self) -> float:
        if self.real:
            return float(self._call("GET", f"/api/v1.0/accounts/{self.acc_id}/funds", params={"currency": "USD"})["total_assets"])
        return float(self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/cash-info")["total_asset"])

    def _sim_orders(self) -> list[dict]:
        self._sim_only("order queries")
        return (self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/orders") or {}).get("orders", [])

    def order_status(self, order_id: str) -> dict:
        for o in self._sim_orders():
            if str(o["order_id"]) == str(order_id):
                return {"status": self.SIM_STATUS.get(int(o["status"]), "UNKNOWN"), "filled_qty": float(o.get("cum_qty") or 0),
                        "avg_price": float(o.get("avg_fill_price") or 0)}
        return {"status": "UNKNOWN", "filled_qty": 0.0, "avg_price": 0.0}

    def open_orders(self) -> list[dict]:
        return [{"id": str(o["order_id"]), "symbol": o["symbol"], "side": "BUY" if int(o["side"]) == 1 else "SELL"}
                for o in self._sim_orders() if int(o["status"]) in (2, 3)]

    def cancel_order(self, order_id: str) -> None:
        self._sim_only("cancel")
        self._call("POST", f"/api/v1.0/sim-trade/{self.acc_id}/orders/{order_id}/cancel", json={})

    def last_price(self, symbol):
        data = self._call("POST", "/api/v1.0/quote/snapshot", json={"code_list": [self._code(symbol)]})
        return float(data["snapshot_list"][0]["last_price"])

    def positions(self):
        if self.real:
            rows = self._call("GET", f"/api/v1.0/accounts/{self.acc_id}/positions") or []
            return [Position(r["code"].split(".", 1)[-1], clean(r["qty"]), float(r["cost_price"]),
                             float(r["nominal_price"]))
                    for r in rows if r.get("position_side", "LONG") == "LONG" and float(r["qty"])]
        rows = self._sim_position_rows()
        return [Position(r["symbol"], clean(r["qty"]), float(r["cost_price"]), float(r["cur_price"]))
                for r in rows if r.get("pstn_type", 0) == 0 and float(r["qty"])]

    def _sim_position_rows(self) -> list[dict]:
        """The service answers the unfiltered call with a backend error for some sim accounts (observed
        on the US margin sim account, market 100), so try with the market filter and remember what works."""
        order = getattr(self, "_pos_market", None)
        candidates = [order] if order is not None else [self.sim_market, US_MARKET_ID, None]
        last: Exception | None = None
        for m in dict.fromkeys(candidates):
            try:
                data = self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/positions",
                                  params={"market": m} if m is not None else None) or {}
            except MoomooError as e:
                last = e
                continue
            self._pos_market = m
            return data.get("positions", [])
        raise last or MoomooError("positions unavailable")

    def cash(self):
        if self.real:
            return float(self._call("GET", f"/api/v1.0/accounts/{self.acc_id}/funds", params={"currency": "USD"})["cash"])
        return float(self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/cash-info")["balance"])

    def _limit_price(self, order: Order) -> float:
        """The sim endpoint rejects plain market orders (observed), so every order goes out as a marketable
        limit: LIMIT_BUFFER_PCT beyond the last price. Behaves like a market order with a slippage cap."""
        if order.price:
            return order.price
        buf = float(os.getenv("MOOMOO_LIMIT_BUFFER_PCT") or 1) / 100
        px = self.last_price(order.symbol)
        return round(px * (1 + buf if order.side == "BUY" else 1 - buf), 2)

    def place_order(self, order):
        try:
            price = self._limit_price(order)
            if self.real:
                body = {"code": self._code(order.symbol), "qty": fmt_qty(order.qty), "side": order.side,
                        "order_type": "LIMIT", "price": f"{price:.4f}", "time_in_force": "DAY", "session": "RTH"}
                d = self._call("POST", f"/api/v1.0/accounts/{self.acc_id}/orders", json=body)
            else:
                body = {"market": self.sim_market, "symbol": order.symbol, "order_type": 1,
                        "order_side": 1 if order.side == "BUY" else 2, "qty": fmt_qty(order.qty), "price": f"{price:.2f}"}
                d = self._call("POST", f"/api/v1.0/sim-trade/{self.acc_id}/orders", json=body)
        except MoomooError as e:
            order.status = "REJECTED"
            if e.code in (-2100, -2101):
                hint = e.extra.get("confirm_id") or e.extra.get("jump_url", "")
                order.reason = f"needs manual confirmation in moomoo ({e.code}: {hint})"
            else:
                order.reason = f"{e.code}: {e}"
            return order
        order.id, order.status, order.price = str(d["order_id"]), "SUBMITTED", price
        return order

    def close(self):
        self.http.close()
