"""Moomoo OpenAPI OAuth 2.1 + PKCE (public client): token storage, refresh, and one-time login.

Flow per https://open.moomoo.com/api/overview/getting-started :
register client -> browser authorize (PKCE S256) -> exchange code -> refresh access token every ~2h.
The docs say not to keep tokens in environment variables, so they live in a 0600 JSON file.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

BASE = "https://webapi.moomoo.com"
DEFAULT_REDIRECT = "http://localhost:60355/callback"
DEFAULT_TOKEN_FILE = "~/.config/trading-bot/moomoo_tokens.json"
EXPIRY_MARGIN = 60  # refresh this many seconds before expiry


class AuthError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str, redirect_uri: str = DEFAULT_REDIRECT) -> str:
    q = urlencode({"client_id": client_id, "code_challenge": challenge, "code_challenge_method": "S256",
                   "redirect_uri": redirect_uri, "response_type": "code", "state": state})
    return f"{BASE}/oauth2/authorize/confirm?{q}"


class TokenStore:
    """JSON file holding client_id, refresh_token and the cached access token."""

    def __init__(self, path: str | None = None):
        self.path = Path(os.path.expanduser(path or os.getenv("MOOMOO_TOKEN_FILE", DEFAULT_TOKEN_FILE)))

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written token file


class OAuthSession:
    """Hands out a valid access token, refreshing transparently. Thread-safe."""

    def __init__(self, store: TokenStore, http: httpx.Client | None = None):
        self.store, self.http, self._lock = store, http or httpx.Client(timeout=15), threading.Lock()

    def access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            d = self.store.load()
            if not d.get("refresh_token") or not d.get("client_id"):
                raise AuthError("Not logged in. Run: python -m bot.moomoo_login")
            if not force_refresh and d.get("access_token") and d.get("expires_at", 0) - EXPIRY_MARGIN > time.time():
                return d["access_token"]
            r = self.http.post(f"{BASE}/oauth2/token", data={
                "grant_type": "refresh_token", "refresh_token": d["refresh_token"], "client_id": d["client_id"]})
            if r.status_code != 200:
                raise AuthError(f"Token refresh failed ({r.status_code}): {r.text[:200]}. Run: python -m bot.moomoo_login")
            body = r.json()
            d.update(access_token=body["access_token"], expires_at=time.time() + int(body.get("expires_in", 7200)),
                     scope=body.get("scope", d.get("scope", "")))
            if body.get("refresh_token"):  # docs say it is not rotated, but accept one if sent
                d["refresh_token"] = body["refresh_token"]
            self.store.save(d)
            return d["access_token"]


def register_client(http: httpx.Client, redirect_uri: str = DEFAULT_REDIRECT) -> str:
    r = http.post(f"{BASE}/oauth2/register", json={
        "redirect_uris": [redirect_uri], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "client_name": "Trading Bot"})
    r.raise_for_status()
    return r.json()["client_id"]


def exchange_code(http: httpx.Client, client_id: str, code: str, verifier: str, redirect_uri: str) -> dict:
    r = http.post(f"{BASE}/oauth2/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": redirect_uri, "code_verifier": verifier})
    if r.status_code != 200:
        raise AuthError(f"Code exchange failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def _wait_for_callback(redirect_uri: str, state: str, timeout: int = 300) -> str:
    u = urlparse(redirect_uri)
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if urlparse(self.path).path != u.path:
                self.send_response(404); self.end_headers(); return
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                result["error"] = "state mismatch"
            elif "code" not in q:
                result["error"] = q.get("error", ["no code returned"])[0]
            else:
                result["code"] = q["code"][0]
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"Authorized. You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    srv = HTTPServer((u.hostname or "localhost", u.port or 80), Handler)
    srv.timeout = 1
    deadline = time.time() + timeout
    while not result and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if "code" not in result:
        raise AuthError(f"Authorization failed: {result.get('error', 'timed out')}")
    return result["code"]


def login(store: TokenStore | None = None, redirect_uri: str = DEFAULT_REDIRECT) -> None:
    """Interactive one-time login. Run on a machine whose browser can reach localhost:<port>."""
    store = store or TokenStore()
    with httpx.Client(timeout=15) as http:
        data = store.load()
        client_id = data.get("client_id") or register_client(http, redirect_uri)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        print("Open this URL in your browser and authorize (choose the scopes you want the bot to have):\n")
        print(authorize_url(client_id, challenge, state, redirect_uri), "\n")
        code = _wait_for_callback(redirect_uri, state)
        body = exchange_code(http, client_id, code, verifier, redirect_uri)
        store.save({"client_id": client_id, "access_token": body["access_token"],
                    "expires_at": time.time() + int(body.get("expires_in", 7200)),
                    "refresh_token": body["refresh_token"], "scope": body.get("scope", "")})
        print(f"Saved tokens to {store.path}. Granted scope: {body.get('scope')}")
