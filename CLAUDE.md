# Project notes
- Read AGENTS.md before touching Expo code (SDK 57; docs.expo.dev may be unreachable from the sandbox, so check types in node_modules).
- Use `npm install <pkg>@<sdk-57 range>` if `npx expo install` can't reach the network (it queries api.expo.dev).
- Checks: `npm run typecheck && npm test` (app), `pytest` (bot). `expo lint` needs network on first run (not configured yet).
- Native modules/network calls go through `lib/providers/*`; screens never call Supabase directly.
- Never put the Supabase service-role key in the Expo app or root `.env`; it lives in `bot/.env` only.
- Live trading is gated by `ALLOW_LIVE=yes` + `TRADE_ENV=REAL`; keep that gate.
