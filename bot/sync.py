"""Mirrors bot state to Supabase (PostgREST) and pulls halt/resume commands from the app.

Uses the service-role key, so it must only ever run on the machine that hosts the bot.
All methods swallow network errors (logged to stderr): monitoring must never stop trading.
"""
import sys

import httpx

from .models import Order, now


class SupabaseSync:
    def __init__(self, url: str, service_key: str, user_id: str, client: httpx.Client | None = None):
        self.user_id = user_id
        self.http = client or httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}",
                     "Content-Type": "application/json"},
            timeout=10,
        )

    def _req(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, path, **kw)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            print(f"[sync] {method} {path} failed: {e}", file=sys.stderr)
            return None

    def push_snapshot(self, snap: dict) -> None:
        uid = self.user_id
        self._req("POST", "/bot_status", headers={"Prefer": "resolution=merge-duplicates"}, json={
            "user_id": uid, "equity": snap["equity"], "cash": snap["cash"], "day_pnl": snap["day_pnl"],
            "halted": snap["halted"], "broker": snap["broker"], "env": snap["env"], "updated_at": now()})
        # Replace positions wholesale so closed ones disappear.
        self._req("DELETE", "/positions", params={"user_id": f"eq.{uid}"})
        if snap["positions"]:
            self._req("POST", "/positions", json=[
                {"user_id": uid, "symbol": p["symbol"], "qty": p["qty"], "avg_price": p["avg_price"],
                 "last_price": p["last_price"]} for p in snap["positions"]])

    def push_order(self, o: Order) -> None:
        self._req("POST", "/orders", json={
            "user_id": self.user_id, "symbol": o.symbol, "side": o.side, "qty": o.qty, "price": o.price,
            "status": o.status, "reason": o.reason, "broker_order_id": o.id, "created_at": o.ts})

    def push_event(self, level: str, msg: str) -> None:
        self._req("POST", "/bot_events", json={"user_id": self.user_id, "level": level, "msg": msg})

    def claim_commands(self) -> list[str]:
        """Return pending commands oldest-first and mark them done."""
        r = self._req("GET", "/bot_commands", params={
            "user_id": f"eq.{self.user_id}", "status": "eq.pending", "order": "id.asc", "select": "id,command"})
        rows = r.json() if r else []
        for row in rows:
            self._req("PATCH", "/bot_commands", params={"id": f"eq.{row['id']}"}, json={"status": "done"})
        return [row["command"] for row in rows]

    # ---- strategy tabs -------------------------------------------------------------------------------
    def claim_backtests(self) -> list[dict]:
        """Atomically move pending runs to 'running' (the status filter on the PATCH makes double-claims a no-op)."""
        r = self._req("GET", "/backtest_runs", params={
            "user_id": f"eq.{self.user_id}", "status": "eq.pending", "order": "created_at.asc",
            "select": "id,strategy,params", "limit": "5"})
        claimed = []
        for row in (r.json() if r else []):
            c = self._req("PATCH", "/backtest_runs", headers={"Prefer": "return=representation"},
                          params={"id": f"eq.{row['id']}", "status": "eq.pending"}, json={"status": "running"})
            if c is not None and c.json():
                claimed.append(row)
        return claimed

    def finish_backtest(self, run_id: str, summary: dict, result: dict) -> None:
        self._req("PATCH", "/backtest_runs", params={"id": f"eq.{run_id}"},
                  json={"status": "done", "summary": summary, "result": result, "finished_at": now()})

    def fail_backtest(self, run_id: str, error: str) -> None:
        self._req("PATCH", "/backtest_runs", params={"id": f"eq.{run_id}"},
                  json={"status": "error", "error": error[:500], "finished_at": now()})

    def get_strategy_configs(self) -> list[dict]:
        r = self._req("GET", "/strategy_configs", params={"user_id": f"eq.{self.user_id}", "select": "strategy,market,enabled,budget,params"})
        return r.json() if r else []

    def push_trade(self, t: dict) -> None:
        self._req("POST", "/strategy_trades", json={
            "user_id": self.user_id, "strategy": t["strategy"], "market": t.get("market", "US"), "mode": t.get("mode", "paper"), "ticker": t["ticker"],
            "entered_at": t["entry_ts"], "exited_at": t["exits"][-1]["ts"], "entry": t["entry"], "stop": t["stop"],
            "shares": t["shares"], "costs": t["costs"], "pnl": t["pnl"], "r_multiple": t["r"],
            "exits": t["exits"], "regime": t.get("regime")})

    def push_engine_status(self, state: dict, market: str = "US") -> None:
        self._req("POST", "/engine_status", headers={"Prefer": "resolution=merge-duplicates"},
                  json={"user_id": self.user_id, "market": market, "state": state, "updated_at": now()})

    def get_market_configs(self) -> list[dict]:
        r = self._req("GET", "/market_configs", params={"user_id": f"eq.{self.user_id}", "select": "market,enabled,symbols,paper_balance"})
        return r.json() if r else []
