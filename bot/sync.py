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
