import type { BacktestResult, EngineRow, JournalTrade, MarketCode, MarketConfig, StrategyCode, StrategyConfig } from './types';

export const PAPER_TRADES_REQUIRED = 100;
export const MAX_BACKTEST_DAYS = 90;

export const STRATEGIES: Record<StrategyCode, { name: string; tab: string; blurb: string }> = {
  A: { name: 'Scalp: VWAP trend pullback', tab: 'Scalp', blurb: 'Buys the first or second pullback in a clean intraday uptrend, 09:45-11:30 ET.' },
  B: { name: 'Intraday trend following', tab: 'Trend', blurb: 'Buys a pullback to the EMA9-EMA20 zone in a trend day, 10:00-12:00 ET; holds until the trend breaks.' },
  C: { name: 'Range trading', tab: 'Range', blurb: 'Buys the low of the initial-balance range on choppy days, 10:30-14:30 ET.' },
};

export const MARKETS: Record<MarketCode, {
  name: string; short: string; currency: string; lot: number; sample: string; pattern: RegExp; hours: string; simulator: string;
}> = {
  US: { name: 'United States', short: 'US', currency: 'USD', lot: 1, sample: 'SPY, QQQ', pattern: /^[A-Z][A-Z.]{0,5}$/,
    hours: '09:30-16:00 New York', simulator: 'Moomoo simulated account' },
  SG: { name: 'Singapore (SGX)', short: 'SG', currency: 'SGD', lot: 100, sample: 'ES3', pattern: /^[A-Z0-9][A-Z0-9.]{0,9}$/,
    hours: '09:00-12:00 and 13:00-17:00 Singapore (verify)', simulator: 'bot-side simulation with real quotes' },
  MY: { name: 'Malaysia (Bursa)', short: 'MY', currency: 'MYR', lot: 100, sample: '1155', pattern: /^[A-Z0-9][A-Z0-9.]{0,9}$/,
    hours: '09:00-12:30 and 14:30-17:00 Malaysia (verify)', simulator: 'bot-side simulation with real quotes' },
};
export const MARKET_CODES: MarketCode[] = ['US', 'SG', 'MY'];

export const money = (n: number, currency = 'USD') =>
  n.toLocaleString(undefined, { style: 'currency', currency, maximumFractionDigits: 0 });

/** The balance a market's budgets must fit inside: US = the Moomoo simulated account, SG/MY = the paper balance you set. */
export function accountLimit(market: MarketCode, botEquity: number | null, markets: MarketConfig[]): number | null {
  if (market === 'US') return botEquity;
  const bal = Number(markets.find((m) => m.market === market)?.paper_balance ?? 0);
  return bal > 0 ? bal : null;
}

export function parseSymbols(raw: string, market: MarketCode): { symbols: string[]; error: string | null } {
  const symbols = raw.split(/[,\s]+/).filter(Boolean).map((t) => t.toUpperCase());
  if (symbols.length < 1 || symbols.length > 3) return { symbols, error: `Use 1 to 3 symbols, e.g. ${MARKETS[market].sample}` };
  if (!symbols.every((t) => MARKETS[market].pattern.test(t))) return { symbols, error: `Not valid ${market} symbols. Example: ${MARKETS[market].sample}` };
  return { symbols, error: null };
}

const num = (v: number | string | null | undefined): number => (v == null ? 0 : Number(v));

/** How the account balance is split across strategies. `limit` is the account's net asset value. */
export function allocation(configs: StrategyConfig[], limit: number | null, exclude?: StrategyCode, market: MarketCode = 'US') {
  const allocated = configs.filter((c) => (c.market ?? 'US') === market && c.strategy !== exclude).reduce((s, c) => s + num(c.budget), 0);
  return { allocated, remaining: limit == null ? null : Math.max(0, limit - allocated) };
}

/** Client-side mirror of the database trigger (the trigger stays the source of truth). */
export function validateBudget(
  raw: string,
  strategy: StrategyCode,
  configs: StrategyConfig[],
  limit: number | null,
  market: MarketCode = 'US',
): { value: number | null; error: string | null } {
  const text = raw.trim().replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return { value: null, error: 'Enter an amount like 5000 or 5000.50' };
  const value = Number(text);
  if (limit == null) {
    return { value, error: market === 'US' ? 'Account balance unknown yet. Start the bot first.' : `Set the ${market} paper balance in Settings first.` };
  }
  const { allocated, remaining } = allocation(configs, limit, strategy, market);
  const cur = MARKETS[market].currency;
  if (value > (remaining ?? 0)) {
    return { value, error: `Over the limit: ${money(limit, cur)} balance, ${money(allocated, cur)} already used by other strategies.` };
  }
  return { value, error: null };
}

export const fmt = (n: number) => n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

export function isIsoDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

export function validateRange(start: string, end: string, today: Date): string | null {
  if (!isIsoDate(start) || !isIsoDate(end)) return 'Dates must look like 2026-09-01';
  const s = new Date(`${start}T00:00:00Z`).getTime();
  const e = new Date(`${end}T00:00:00Z`).getTime();
  if (s >= e) return 'Start must be before end';
  if (e > today.getTime() + 86_400_000) return 'End cannot be in the future';
  if ((e - s) / 86_400_000 > MAX_BACKTEST_DAYS) return `Maximum range is ${MAX_BACKTEST_DAYS} days`;
  return null;
}

export function defaultRange(today: Date, days = 30): { start: string; end: string } {
  const end = today.toISOString().slice(0, 10);
  const start = new Date(today.getTime() - days * 86_400_000).toISOString().slice(0, 10);
  return { start, end };
}

export function parseTickers(raw: string, market: MarketCode = 'US'): { tickers: string[]; error: string | null } {
  const { symbols, error } = parseSymbols(raw, market);
  return { tickers: symbols, error };
}

/** Evenly thin the equity curve to at most `max` points for the bar chart. */
export function downsample<T>(points: T[], max: number): T[] {
  if (points.length <= max) return points;
  const step = (points.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => points[Math.round(i * step)]);
}

export function equityBars(curve: BacktestResult['equity_curve'], start: number, max = 48) {
  const pts = downsample(curve.map((p) => num(p.equity)), max);
  if (pts.length === 0) return [];
  const lo = Math.min(start, ...pts);
  const hi = Math.max(start, ...pts);
  const span = hi - lo || 1;
  return pts.map((v) => ({ value: v, height: Math.max(2, ((v - lo) / span) * 100), up: v >= start }));
}

export function journalProgress(trades: JournalTrade[], mode: 'paper' | 'live' = 'paper') {
  const closed = trades.filter((t) => t.mode === mode && t.exited_at != null);
  const rs = closed.map((t) => num(t.r_multiple));
  const wins = closed.filter((t) => num(t.pnl) > 0).length;
  return {
    count: closed.length,
    required: PAPER_TRADES_REQUIRED,
    pct: Math.min(100, (closed.length / PAPER_TRADES_REQUIRED) * 100),
    winRate: closed.length ? (wins / closed.length) * 100 : null,
    avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    pnl: closed.reduce((s, t) => s + num(t.pnl), 0),
  };
}

export const fmtR = (r: number | null | undefined) => (r == null ? '-' : `${r >= 0 ? '+' : ''}${r.toFixed(2)}R`);

export const ENGINE_OFFLINE_AFTER_S = 90;

/** One honest line about the engine plus a severity for colouring. */
export function engineSummary(row: EngineRow | null, now: Date, code: StrategyCode, marketOn = true): { text: string; level: 'ok' | 'warn' | 'bad' } {
  if (!marketOn && !row) return { text: 'This market is switched off. Turn it on in Settings > Markets.', level: 'warn' };
  if (!row) return { text: 'Paper engine has not reported yet. Start the bot with LIVE_STRATEGIES=yes.', level: 'warn' };
  const age = (now.getTime() - new Date(row.updated_at).getTime()) / 1000;
  if (age > ENGINE_OFFLINE_AFTER_S) return { text: `Engine offline: last report ${Math.round(age / 60)} min ago.`, level: 'bad' };
  const s = row.state;
  if (s.data_error) return { text: `Cannot get market data: ${s.data_error}`, level: 'bad' };
  if (s.halt_reason) return { text: `New entries halted: ${s.halt_reason}`, level: 'bad' };
  if (s.session === 'closed') return { text: 'Market closed. The engine is waiting for the next session.', level: 'ok' };
  if (!s.feed_ok) return { text: 'Price feed stale: no new entries, and bot-held stops are not being watched.', level: 'bad' };
  if (s.blocked_today) return { text: `No trading today (${s.blocked_today.replace('_', ' ')}).`, level: 'warn' };
  if (!s.enabled.includes(code)) return { text: 'Engine running. This strategy is not enabled or has no budget.', level: 'warn' };
  if (!marketOn) return { text: 'Market switched off: no new entries; any open position is still managed.', level: 'warn' };
  if (s.market && s.market !== 'US') return { text: `Engine running on the bot's simulated ${s.currency ?? ''} account (real quotes).`, level: 'ok' };
  return { text: 'Engine running on the simulated account.', level: 'ok' };
}

/** Plain-language names for the funnel counters the backtester records. `lost` rows are where setups were removed. */
const FUNNEL: Record<string, { label: string; lost?: boolean }> = {
  days_evaluated: { label: 'Trading days checked' },
  window_minutes: { label: 'Minutes inside the entry window' },
  window_bars: { label: '5-minute bars inside the entry window' },
  regime_failed_warming_up: { label: 'Indicators still warming up', lost: true },
  regime_failed_close_above_vwap: { label: 'Price below VWAP', lost: true },
  regime_failed_vwap_rising: { label: 'VWAP not rising', lost: true },
  regime_failed_ema9_above_ema20: { label: 'EMA9 not above EMA20', lost: true },
  regime_failed_emas_rising: { label: 'EMA9 and EMA20 not both rising', lost: true },
  regime_failed_vwap_crosses_ok: { label: 'Too many VWAP crossings (choppy)', lost: true },
  regime_failed_broke_opening_range: { label: 'Never closed above the opening range', lost: true },
  regime_failed_above_opening_range: { label: 'Price not above the opening range', lost: true },
  regime_failed_too_few_vwap_crosses: { label: 'Not choppy enough for a range day', lost: true },
  regime_failed_range_width: { label: 'Range too narrow or too wide', lost: true },
  regime_minutes: { label: 'Minutes with a valid uptrend' },
  regime_bars: { label: 'Bars with a valid setup regime' },
  days_regime_on: { label: 'Days with a valid uptrend' },
  days_killed: { label: 'Days stopped by the range-break rule', lost: true },
  pullbacks: { label: 'Pullbacks to the 5-minute EMA9' },
  pullback_bars: { label: 'Pullback bars' },
  setups: { label: 'Bounces at the bottom of the range' },
  limit_trades_per_ticker: { label: 'Already traded this symbol twice today', lost: true },
  triggers: { label: 'Bounce triggers (price recovered)' },
  rejected_stop_too_wide: { label: 'Stop wider than the strategy allows', lost: true },
  rejected_stop_band: { label: 'Stop outside the allowed distance band', lost: true },
  rejected_key_level: { label: 'A key level blocks the first target', lost: true },
  rejected_target_too_close: { label: 'Target less than 2R away', lost: true },
  signals: { label: 'Signals produced' },
  signals_seen: { label: 'Signals reaching the risk rules' },
  ignored_position_already_open: { label: 'Another trade was already open', lost: true },
  blocked_drawdown_breaker: { label: 'Stopped by the 10% drawdown breaker', lost: true },
  blocked_event_day_early_entry: { label: 'Too early on an event day', lost: true },
  skipped_cost: { label: 'Fees too high compared with the risk', lost: true },
  skipped_shares: { label: 'Budget too small for even one share', lost: true },
  skipped_invalid: { label: 'Invalid entry/stop', lost: true },
  orders_placed: { label: 'Orders placed' },
  entries_not_filled: { label: 'Orders the price ran away from', lost: true },
  entries_filled: { label: 'Trades taken' },
};

export type FunnelRow = { key: string; label: string; count: number; lost: boolean };

/** Ordered rows for the strategy's own stages followed by the engine's gates; zero counts are hidden. */
export function funnelRows(funnel: Record<string, Record<string, number>> | undefined, code: StrategyCode): FunnelRow[] {
  if (!funnel) return [];
  const rows: FunnelRow[] = [];
  for (const section of [code, 'engine']) {
    for (const [key, count] of Object.entries(funnel[section] ?? {})) {
      if (!count) continue;
      const meta = FUNNEL[key];
      const blocked = key.startsWith('blocked: ');
      rows.push({
        key: `${section}.${key}`,
        label: blocked ? `Blocked by risk rule: ${key.slice(9)}` : meta?.label ?? key.replace(/_/g, ' '),
        count,
        lost: blocked || Boolean(meta?.lost),
      });
    }
  }
  return rows;
}
