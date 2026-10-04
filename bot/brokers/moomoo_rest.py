"""Moomoo REST adapter (no OpenD). https://open.moomoo.com/api/overview/getting-started

Two backends share the quote endpoints:
  SIMULATE -> /api/v1.0/sim-trade/...   (envelope {ret_code, data})
  REAL     -> /api/v1.0/accounts/...    (envelope {s: ok|error, d})
REAL orders that need confirmation (errcode -2100) or a browser step (-2101) are NOT auto-confirmed:
the order is marked REJECTED with the confirm_id in its reason so a human decides.

Unverified against the live service (docs only): whether sim-trade endpoints accept the Bearer token,
and Bearer scopes needed (trade:read/trade:write/quote:read).
"""
import time
from datetime import date

import httpx

from ..models import Order, Position
from .base import Broker
from ..moomoo_oauth import BASE, AuthError, OAuthSession, TokenStore

US_MARKET_ID = 2


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
        us = [a for a in accts if a.get("market_id") == US_MARKET_ID]
        if not us:
            raise MoomooError("No US simulated account found")
        return str(us[0]["account_id"])

    @staticmethod
    def _code(symbol: str) -> str:
        return symbol if "." in symbol else f"US.{symbol}"

    # ---- Broker interface -----------------------------------------------------------------
    def history(self, symbol, bars):
        data = self._call("GET", f"/api/v1.0/quote/{self._code(symbol)}/history-kline",
                          params={"end": date.today().isoformat(), "ktype": 2, "autype": 1, "num": min(bars + 5, 370)})
        rows = sorted(data.get("kline_list", []), key=lambda k: k["time_key"])
        return [float(k["close"]) for k in rows][-bars:]

    def last_price(self, symbol):
        data = self._call("POST", "/api/v1.0/quote/snapshot", json={"code_list": [self._code(symbol)]})
        return float(data["snapshot_list"][0]["last_price"])

    def positions(self):
        if self.real:
            rows = self._call("GET", f"/api/v1.0/accounts/{self.acc_id}/positions") or []
            return [Position(r["code"].split(".", 1)[-1], int(float(r["qty"])), float(r["cost_price"]),
                             float(r["nominal_price"]))
                    for r in rows if r.get("position_side", "LONG") == "LONG" and float(r["qty"])]
        rows = (self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/positions") or {}).get("positions", [])
        return [Position(r["symbol"], int(float(r["qty"])), float(r["cost_price"]), float(r["cur_price"]))
                for r in rows if r.get("pstn_type", 0) == 0 and float(r["qty"])]

    def cash(self):
        if self.real:
            return float(self._call("GET", f"/api/v1.0/accounts/{self.acc_id}/funds", params={"currency": "USD"})["cash"])
        return float(self._call("GET", f"/api/v1.0/sim-trade/{self.acc_id}/cash-info")["balance"])

    def place_order(self, order):
        try:
            if self.real:
                body = {"code": self._code(order.symbol), "qty": str(order.qty), "side": order.side,
                        "order_type": "LIMIT" if order.price else "MARKET", "time_in_force": "DAY", "session": "RTH"}
                if order.price:
                    body["price"] = f"{order.price:.4f}"
                d = self._call("POST", f"/api/v1.0/accounts/{self.acc_id}/orders", json=body)
            else:
                body = {"market": US_MARKET_ID, "symbol": order.symbol, "order_type": 1 if order.price else 3,
                        "order_side": 1 if order.side == "BUY" else 2, "qty": str(order.qty)}
                if order.price:
                    body["price"] = f"{order.price:.4f}"
                d = self._call("POST", f"/api/v1.0/sim-trade/{self.acc_id}/orders", json=body)
        except MoomooError as e:
            order.status = "REJECTED"
            if e.code in (-2100, -2101):
                hint = e.extra.get("confirm_id") or e.extra.get("jump_url", "")
                order.reason = f"needs manual confirmation in moomoo ({e.code}: {hint})"
            else:
                order.reason = f"{e.code}: {e}"
            return order
        order.id, order.status = str(d["order_id"]), "SUBMITTED"
        return order

    def close(self):
        self.http.close()
