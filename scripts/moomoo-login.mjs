// One-time Moomoo OAuth 2.1 + PKCE login. Node 18+, no dependencies.
// Writes the same token file that bot/moomoo_oauth.py reads, so the bot can run on any machine you copy it to.
//   node scripts/moomoo-login.mjs
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const BASE = process.env.MOOMOO_BASE || 'https://webapi.moomoo.com';
const REDIRECT = 'http://localhost:60355/callback';
const tokenFile = (process.env.MOOMOO_TOKEN_FILE || path.join(os.homedir(), '.config', 'trading-bot', 'moomoo_tokens.json'))
  .replace(/^~/, os.homedir());

const b64url = (buf) => buf.toString('base64url');
const existing = fs.existsSync(tokenFile) ? JSON.parse(fs.readFileSync(tokenFile, 'utf8')) : {};

async function post(url, body, json) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': json ? 'application/json' : 'application/x-www-form-urlencoded' },
    body: json ? JSON.stringify(body) : new URLSearchParams(body),
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}: ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

let clientId = existing.client_id;
if (!clientId) {
  clientId = (await post(`${BASE}/oauth2/register`, {
    redirect_uris: [REDIRECT], token_endpoint_auth_method: 'none',
    grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], client_name: 'Trading Bot',
  }, true)).client_id;
}

const verifier = b64url(crypto.randomBytes(48));
const challenge = b64url(crypto.createHash('sha256').update(verifier).digest());
const state = b64url(crypto.randomBytes(16));
const authUrl = `${BASE}/oauth2/authorize/confirm?` + new URLSearchParams({
  client_id: clientId, code_challenge: challenge, code_challenge_method: 'S256',
  redirect_uri: REDIRECT, response_type: 'code', state,
});

const code = await new Promise((resolve, reject) => {
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, REDIRECT);
    if (u.pathname !== '/callback') { res.writeHead(404).end(); return; }
    const ok = u.searchParams.get('state') === state && u.searchParams.get('code');
    res.writeHead(200, { 'Content-Type': 'text/plain' })
      .end(ok ? 'Authorized. You can close this tab and return to the terminal.' : 'Authorization failed. See terminal.');
    server.close();
    ok ? resolve(u.searchParams.get('code')) : reject(new Error(`bad callback: ${u.search}`));
  });
  server.listen(60355, 'localhost', () => {
    console.log('Open this URL in your browser, sign in, and authorize:\n\n' + authUrl + '\n');
  });
  setTimeout(() => { server.close(); reject(new Error('timed out after 5 minutes')); }, 300_000).unref();
});

const t = await post(`${BASE}/oauth2/token`, {
  grant_type: 'authorization_code', code, client_id: clientId, redirect_uri: REDIRECT, code_verifier: verifier,
});
fs.mkdirSync(path.dirname(tokenFile), { recursive: true });
fs.writeFileSync(tokenFile, JSON.stringify({
  client_id: clientId, access_token: t.access_token, expires_at: Date.now() / 1000 + (t.expires_in ?? 7200),
  refresh_token: t.refresh_token, scope: t.scope ?? '',
}), { mode: 0o600 });
console.log(`Saved tokens to ${tokenFile}\nGranted scope: ${t.scope}`);
