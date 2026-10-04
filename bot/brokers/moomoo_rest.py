"""Moomoo REST adapter (no OpenD). https://open.moomoo.com/api/overview/getting-started

Two backends share the quote endpoints:
  SIMULATE -> /api/v1.0/sim-trade/...   (envelope {ret_code, data})
  REAL     -> /api/v1.0/accounts/...    (envelope {s: ok|error, d})
REAL orders that need confirmation (errcode -2100) or a browser step (-2101) are NOT auto-confirmed:
the order is marked REJECTED with the confirm_id in its reason so a human decides.

Unverified against the live service (docs only): whether sim-trade endpoints accept the Bearer token,
and Bearer scopes needed (trade:read/trade:write/quote:read).
"""
import os
import time
from datetime import date, datetime

import httpx

from ..intraday.bars import NY
from ..intraday.market import US, Market
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
                 http: httpx.Client | None = None, sleep=time.sleep):
        self.real = trade_env == "REAL"
        self.http = http or httpx.Client(base_url=BASE, timeout=15)
        self.auth = session or OAuthSession(TokenStore())
        self._sleep = sleep
        self.sim_market = int(os.getenv("MOOMOO_SIM_MARKET") or 0) or US_MARKET_ID
        self.acc_id = acc_id or self._discover_account()

    # ---- plumbing -------------------------------------------------------------------------
    def _call(self, method: str, path: str, **kw):
        """HTTP with bearer auth, one refresh-and-retry on 401, backoff on 429. Returns unwrapped payload."""
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

    def intraday_bars(self, symbol: str, start: date, end: date, market: Market = US, ktype: int = 1, max_pages: int = 120):
        """1-minute (ktype=1) bars incl. pre/after-market, oldest first, paging backwards from `end`.
        UNVERIFIED against the live service: the paging contract (next_time passed back as `end`) is from the docs;
        check coverage in the result's `data` section before trusting a backtest."""
        from ..intraday.data import bars_from_moomoo
        rows, seen, cursor = [], set(), end.isoformat()
        for _ in range(max_pages):
            params = {"start": start.isoformat(), "end": cursor, "ktype": ktype, "autype": 1, "num": 370}
            if market.extended_hours:
                params["extended_time"] = 1   # pre/after-market bars (US only)
            data = self._call("GET", f"/api/v1.0/quote/{self._code(symbol, market)}/history-kline", params=params)
            page = [k for k in data.get("kline_list", []) if k["time_key"] not in seen]
            if not page:
                break
            seen.update(k["time_key"] for k in page)
            rows += page
            nxt = data.get("next_time")
            if not nxt or min(k["time_key"] for k in page) / 1000 <= datetime.combine(
                    start, datetime.min.time()).timestamp():
                break
            cursor = str(nxt)
            self._sleep(0.15)
        bars = bars_from_moomoo(rows, market.tz)
        return [b for b in bars if start <= b.ts.date() <= end]

    def basic_info(self, codes: list[str]) -> list[dict]:
        """Static facts (name, board lot, exchange, state) for full codes like 'MY.1155'. Unknown codes are simply absent."""
        data = self._call("POST", "/api/v1.0/quote/stock-basicinfo", json={"code_list": codes})
        return data.get("basic_list", [])

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
        from ..intraday.data import bars_from_moomoo
        params = {"end": datetime.now(market.tz).date().isoformat(), "ktype": 1, "autype": 1, "num": n}
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
            return [Position(r["code"].split(".", 1)[-1], int(float(r["qty"])), float(r["cost_price"]),
                             float(r["nominal_price"]))
                    for r in rows if r.get("position_side", "LONG") == "LONG" and float(r["qty"])]
        rows = self._sim_position_rows()
        return [Position(r["symbol"], int(float(r["qty"])), float(r["cost_price"]), float(r["cur_price"]))
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
                body = {"code": self._code(order.symbol), "qty": str(order.qty), "side": order.side,
                        "order_type": "LIMIT", "price": f"{price:.4f}", "time_in_force": "DAY", "session": "RTH"}
                d = self._call("POST", f"/api/v1.0/accounts/{self.acc_id}/orders", json=body)
            else:
                body = {"market": self.sim_market, "symbol": order.symbol, "order_type": 1,
                        "order_side": 1 if order.side == "BUY" else 2, "qty": str(order.qty), "price": f"{price:.2f}"}
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
