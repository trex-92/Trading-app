import json
import os
import stat
import time

import httpx
import pytest

from bot.brokers.moomoo_rest import MoomooRestBroker
from bot.models import Order
from bot.moomoo_oauth import AuthError, OAuthSession, TokenStore, authorize_url, pkce_pair


def store(tmp_path, **over):
    s = TokenStore(str(tmp_path / "t.json"))
    s.save({"client_id": "cid", "access_token": "AT", "expires_at": time.time() + 3600,
            "refresh_token": "RT", **over})
    return s


def broker(tmp_path, handler, env="SIMULATE", acc="1", **store_over):
    http = httpx.Client(base_url="https://webapi.moomoo.com", transport=httpx.MockTransport(handler))
    sess = OAuthSession(store(tmp_path, **store_over), httpx.Client(transport=httpx.MockTransport(handler)))
    return MoomooRestBroker(env, acc, sess, http, sleep=lambda s: None)


def ok_sim(data):
    return httpx.Response(200, json={"ret_code": 0, "data": data})


def ok_trd(d):
    return httpx.Response(200, json={"s": "ok", "d": d})


def test_pkce_challenge_matches_verifier():
    import base64, hashlib
    v, c = pkce_pair()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    url = authorize_url("cid", c, "st")
    assert "code_challenge_method=S256" in url and "state=st" in url and "client_id=cid" in url


def test_token_file_is_private(tmp_path):
    s = store(tmp_path)
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600


def test_refreshes_expired_token_and_persists(tmp_path):
    calls = []

    def h(req):
        calls.append(req)
        assert req.url.path == "/oauth2/token" and b"grant_type=refresh_token" in req.content
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 7200, "scope": "trade:read"})

    s = store(tmp_path, expires_at=time.time() - 5)
    sess = OAuthSession(s, httpx.Client(transport=httpx.MockTransport(h)))
    assert sess.access_token() == "NEW"
    assert sess.access_token() == "NEW" and len(calls) == 1  # cached
    assert s.load()["refresh_token"] == "RT"


def test_not_logged_in_raises(tmp_path):
    with pytest.raises(AuthError):
        OAuthSession(TokenStore(str(tmp_path / "none.json"))).access_token()


def test_sim_reads_and_order_body(tmp_path):
    seen = {}

    def h(req):
        p = req.url.path
        assert req.headers["authorization"] == "Bearer AT"
        if p.endswith("/history-kline"):
            return ok_sim({"kline_list": [{"time_key": 2, "close": 11.0}, {"time_key": 1, "close": 10.0}]})
        if p.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 12.5}]})
        if p.endswith("/cash-info"):
            return ok_sim({"balance": "1000"})
        if p.endswith("/positions"):
            return ok_sim({"positions": [{"symbol": "AAPL", "qty": "3", "cost_price": "10", "cur_price": "12", "pstn_type": 0},
                                         {"symbol": "X", "qty": "0", "cost_price": "1", "cur_price": "1"}]})
        if p.endswith("/orders"):
            seen.update(json.loads(req.content))
            return ok_sim({"order_id": "77"})
        raise AssertionError(p)

    b = broker(tmp_path, h)
    assert b.history("AAPL", 2) == [10.0, 11.0]  # sorted ascending
    assert b.last_price("AAPL") == 12.5 and b.cash() == 1000.0
    pos = b.positions()
    assert len(pos) == 1 and pos[0].symbol == "AAPL" and pos[0].qty == 3
    o = b.place_order(Order("AAPL", "SELL", 2))
    assert (o.status, o.id) == ("SUBMITTED", "77")
    # marketable limit: 1% below the 12.5 last price for a sell; plain market orders are rejected by the sim
    assert seen == {"market": 2, "symbol": "AAPL", "order_type": 1, "order_side": 2, "qty": "2", "price": "12.38"}
    assert o.price == 12.38


def test_real_order_body_and_confirmation_is_not_auto_confirmed(tmp_path):
    bodies = []

    def h(req):
        if req.url.path.endswith("/order_confirm"):
            raise AssertionError("must never auto-confirm")
        if req.url.path.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 100.0}]})
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"s": "error", "errcode": -2100, "errmsg": "Order confirmation required.",
                                         "need_order_confirm": True, "confirm_id": "abc"})

    b = broker(tmp_path, h, env="REAL", acc="9")
    o = b.place_order(Order("AAPL", "BUY", 1))
    assert bodies[0] == {"code": "US.AAPL", "qty": "1", "side": "BUY", "order_type": "LIMIT", "price": "101.0000",
                         "time_in_force": "DAY", "session": "RTH"}
    assert o.status == "REJECTED" and "abc" in o.reason


def test_401_triggers_one_refresh_then_retries(tmp_path):
    n = {"api": 0}

    def h(req):
        if req.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "FRESH", "expires_in": 7200})
        n["api"] += 1
        if req.headers["authorization"] == "Bearer AT":
            return httpx.Response(401, json={})
        return ok_sim({"balance": "5"})

    assert broker(tmp_path, h).cash() == 5.0 and n["api"] == 2


def test_429_backs_off_and_error_envelope_becomes_rejected_order(tmp_path):
    n = {"c": 0}

    def h(req):
        if req.url.path.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 10.0}]})
        n["c"] += 1
        if n["c"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"ret_code": -3, "ret_msg": "bad qty"})

    o = broker(tmp_path, h).place_order(Order("AAPL", "BUY", 1))
    assert n["c"] == 2 and o.status == "REJECTED" and "bad qty" in o.reason


def test_account_discovery(tmp_path):
    def sim(req):
        return ok_sim({"accounts": [{"account_id": "1", "market_id": 1}, {"account_id": "2", "market_id": 100}]})
    b = broker(tmp_path, sim, acc="")
    assert b.acc_id == "2" and b.sim_market == 100  # real service labels the US sim account 100

    def real(req):
        return ok_trd({"accounts": [{"account_id": 5}, {"account_id": 6}]})
    with pytest.raises(Exception, match="MOOMOO_ACC_ID"):
        broker(tmp_path, real, env="REAL", acc="")


def test_sim_positions_falls_back_to_market_filter(tmp_path):
    seen = []

    def h(req):
        seen.append(req.url.params.get("market"))
        if req.url.params.get("market") != "100":
            return httpx.Response(200, json={"ret_code": -5, "ret_msg": "backend business error"})
        return ok_sim({"positions": [{"symbol": "AAPL", "qty": "1", "cost_price": "1", "cur_price": "2"}]})

    b = broker(tmp_path, h)
    b.sim_market = 100
    assert [p.symbol for p in b.positions()] == ["AAPL"]
    b.positions()
    assert seen == ["100", "100"]  # remembered the working filter


def test_paper_engine_calls(tmp_path):
    def h(req):
        p = req.url.path
        if p.endswith("/quote/snapshot"):
            return ok_sim({"snapshot_list": [{"code": "US.SPY", "last_price": 601.25, "bid_price": 601.2, "ask_price": 601.3,
                                              "update_time": 1790000000000}]})
        if p.endswith("/history-kline"):
            return ok_sim({"kline_list": [{"time_key": 1790000000000 + 60000 * i, "open": 1, "high": 2, "low": 0.5,
                                           "close": 1.5, "volume": 10} for i in range(5)]})
        if p.endswith("/cash-info"):
            return ok_sim({"balance": "1", "total_asset": "999.5"})
        if p.endswith("/cancel"):
            seen["cancel"] = p
            return ok_sim({"order_id": "9"})
        if p.endswith("/orders"):
            return ok_sim({"orders": [
                {"order_id": "1", "side": 1, "symbol": "SPY", "status": 4, "cum_qty": "10", "avg_fill_price": "600.5"},
                {"order_id": "2", "side": 2, "symbol": "QQQ", "status": 3, "cum_qty": "5", "avg_fill_price": "500"},
                {"order_id": "3", "side": 1, "symbol": "SPY", "status": 5, "cum_qty": "0", "avg_fill_price": "0"}]})
        raise AssertionError(p)

    seen = {}
    b = broker(tmp_path, h)
    snap = b.snapshot(["SPY"])["SPY"]
    assert snap["last"] == 601.25 and snap["ts"].tzinfo is not None and snap["bid"] == 601.2
    assert len(b.recent_bars("SPY", 3)) == 3 and b.equity() == 999.5
    assert b.order_status("1") == {"status": "FILLED", "filled_qty": 10.0, "avg_price": 600.5}
    assert b.order_status("2")["status"] == "PARTIAL" and b.order_status("404")["status"] == "UNKNOWN"
    assert [(o["id"], o["side"]) for o in b.open_orders()] == [("2", "SELL")]
    b.cancel_order("2")
    assert seen["cancel"].endswith("/orders/2/cancel")


def test_paper_engine_calls_refuse_the_real_account(tmp_path):
    b = broker(tmp_path, lambda req: ok_trd({}), env="REAL", acc="9")
    for call in (lambda: b.order_status("1"), lambda: b.open_orders(), lambda: b.cancel_order("1")):
        with pytest.raises(NotImplementedError):
            call()
