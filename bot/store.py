import json
import sqlite3
import threading

from .models import Order, now


class Store:
    """SQLite log of orders and events; the API reads from here."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders(pk INTEGER PRIMARY KEY, ts TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS events(pk INTEGER PRIMARY KEY, ts TEXT, level TEXT, msg TEXT);
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        """)

    def add_order(self, o: Order):
        with self.lock, self.db:
            self.db.execute("INSERT INTO orders(ts,data) VALUES(?,?)", (o.ts, json.dumps(o.to_dict())))

    def orders(self, limit=100) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT data FROM orders ORDER BY pk DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def orders_today(self) -> int:
        day = now()[:10]
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM orders WHERE ts LIKE ?", (day + "%",)).fetchone()[0]

    def log(self, level: str, msg: str):
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(ts,level,msg) VALUES(?,?,?)", (now(), level, msg))

    def events(self, limit=100) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT ts,level,msg FROM events ORDER BY pk DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": t, "level": l, "msg": m} for t, l, m in rows]

    def set(self, k: str, v):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))

    def get(self, k: str, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default
