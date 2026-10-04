import type { BacktestResult, JournalTrade, StrategyCode, StrategyConfig } from './types';

export const PAPER_TRADES_REQUIRED = 100;
export const MAX_BACKTEST_DAYS = 90;

export const STRATEGIES: Record<StrategyCode, { name: string; tab: string; blurb: string }> = {
  A: { name: 'Scalp: VWAP trend pullback', tab: 'Scalp', blurb: 'Buys the first or second pullback in a clean intraday uptrend, 09:45-11:30 ET.' },
  B: { name: 'Intraday trend following', tab: 'Trend', blurb: 'Buys a pullback to the EMA9-EMA20 zone in a trend day, 10:00-12:00 ET; holds until the trend breaks.' },
  C: { name: 'Range trading', tab: 'Range', blurb: 'Buys the low of the initial-balance range on choppy days, 10:30-14:30 ET.' },
};

const num = (v: number | string | null | undefined): number => (v == null ? 0 : Number(v));

/** How the account balance is split across strategies. `limit` is the account's net asset value. */
export function allocation(configs: StrategyConfig[], limit: number | null, exclude?: StrategyCode) {
  const allocated = configs.filter((c) => c.strategy !== exclude).reduce((s, c) => s + num(c.budget), 0);
  return { allocated, remaining: limit == null ? null : Math.max(0, limit - allocated) };
}

/** Client-side mirror of the database trigger (the trigger stays the source of truth). */
export function validateBudget(
  raw: string,
  strategy: StrategyCode,
  configs: StrategyConfig[],
  limit: number | null,
): { value: number | null; error: string | null } {
  const text = raw.trim().replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return { value: null, error: 'Enter an amount like 5000 or 5000.50' };
  const value = Number(text);
  if (limit == null) return { value, error: 'Account balance unknown yet. Start the bot first.' };
  const { allocated, remaining } = allocation(configs, limit, strategy);
  if (value > (remaining ?? 0)) {
    return { value, error: `Over the limit: ${fmt(limit)} balance, ${fmt(allocated)} already used by other strategies.` };
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

export function parseTickers(raw: string): { tickers: string[]; error: string | null } {
  const tickers = raw.split(/[,\s]+/).filter(Boolean).map((t) => t.toUpperCase());
  if (tickers.length < 1 || tickers.length > 3) return { tickers, error: 'Use 1 to 3 symbols, e.g. SPY, QQQ' };
  if (!tickers.every((t) => /^[A-Z][A-Z.]{0,5}$/.test(t))) return { tickers, error: 'Symbols may only contain letters' };
  return { tickers, error: null };
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
