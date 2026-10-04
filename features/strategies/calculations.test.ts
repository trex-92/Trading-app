import {
  accountLimit, allocation, funnelRows, limitsOf, limitsText, money, parseMaxTrade, withLimits, parseSymbols, defaultRange, engineSummary, downsample, equityBars, isIsoDate, journalProgress, parseTickers, validateBudget, validateRange,
} from './calculations';
import type { JournalTrade, StrategyConfig } from './types';

const cfg = (strategy: 'A' | 'B' | 'C', budget: number, market: 'US' | 'SG' | 'MY' = 'US'): StrategyConfig => ({
  user_id: 'u', strategy, market, enabled: false, budget, params: {}, updated_at: '',
});

describe('budget allocation', () => {
  const configs = [cfg('A', 400_000), cfg('B', 300_000)];

  it('sums other strategies and leaves the remainder', () => {
    expect(allocation(configs, 1_000_000)).toEqual({ allocated: 700_000, remaining: 300_000 });
    expect(allocation(configs, 1_000_000, 'A')).toEqual({ allocated: 300_000, remaining: 700_000 });
    expect(allocation(configs, null).remaining).toBeNull();
  });

  it('accepts a budget inside the account limit, including editing your own', () => {
    expect(validateBudget('300000', 'C', configs, 1_000_000).error).toBeNull();
    // A currently holds 400k; editing it can use everything B is not using (1,000,000 - 300,000)
    expect(validateBudget('700,000', 'A', configs, 1_000_000).error).toBeNull();
    expect(validateBudget('700000.01', 'A', configs, 1_000_000).error).toMatch(/Over the limit/);
  });

  it('rejects over-limit, malformed and unknown-balance budgets', () => {
    expect(validateBudget('300000.01', 'C', configs, 1_000_000).error).toMatch(/Over the limit/);
    expect(validateBudget('abc', 'C', configs, 1_000_000).error).toMatch(/Enter an amount/);
    expect(validateBudget('-5', 'C', configs, 1_000_000).error).toMatch(/Enter an amount/);
    expect(validateBudget('100', 'C', configs, null).error).toMatch(/unknown/);
  });
});

describe('inputs', () => {
  it('validates ISO dates strictly', () => {
    expect(isIsoDate('2026-09-01')).toBe(true);
    expect(isIsoDate('2026-02-30')).toBe(false);
    expect(isIsoDate('09/01/2026')).toBe(false);
  });

  it('validates the backtest range', () => {
    const today = new Date('2026-10-05T12:00:00Z');
    expect(validateRange('2026-09-01', '2026-09-30', today)).toBeNull();
    expect(validateRange('2026-09-30', '2026-09-01', today)).toMatch(/before/);
    expect(validateRange('2025-08-01', '2026-09-30', today)).toMatch(/Maximum/);
    expect(validateRange('2026-09-01', '2027-01-01', today)).toMatch(/future/);
    expect(validateRange('x', '2026-09-30', today)).toMatch(/Dates/);
  });

  it('defaults to the last 30 days', () => {
    expect(defaultRange(new Date('2026-10-05T12:00:00Z'))).toEqual({ start: '2026-09-05', end: '2026-10-05' });
  });

  it('parses tickers', () => {
    expect(parseTickers('spy, qqq').tickers).toEqual(['SPY', 'QQQ']);
    expect(parseTickers('').error).toMatch(/1 to 10/);
    expect(parseTickers('a b c d e f g h i j k').error).toMatch(/1 to 10/);
    expect(parseTickers('a b c d e f g h i j').error).toBeNull();
    expect(parseTickers('SPY SPY').error).toMatch(/only be listed once/);
    expect(parseTickers('SPY; DROP').error).toMatch(/Not valid US symbols/);
  });
});

describe('charts and journal', () => {
  it('downsamples keeping first and last point', () => {
    const xs = Array.from({ length: 100 }, (_, i) => i);
    const out = downsample(xs, 10);
    expect(out).toHaveLength(10);
    expect(out[0]).toBe(0);
    expect(out[9]).toBe(99);
    expect(downsample([1, 2], 10)).toEqual([1, 2]);
  });

  it('scales equity bars between the low and high including the start balance', () => {
    const bars = equityBars([{ ts: 'a', equity: 90 }, { ts: 'b', equity: 110 }], 100);
    expect(bars.map((b) => b.up)).toEqual([false, true]);
    expect(bars[1].height).toBe(100);
    expect(equityBars([], 100)).toEqual([]);
  });

  it('tracks progress to 100 paper trades', () => {
    const t = (pnl: number, r: number, exited: boolean): JournalTrade => ({
      id: String(Math.random()), strategy: 'A', mode: 'paper', ticker: 'SPY', entered_at: 'x',
      exited_at: exited ? 'y' : null, entry: 1, shares: 1, pnl, r_multiple: r,
    });
    const p = journalProgress([t(10, 1, true), t(-5, -0.5, true), t(0, 0, false)]);
    expect(p.count).toBe(2); // open trade is not counted
    expect(p.winRate).toBe(50);
    expect(p.avgR).toBeCloseTo(0.25);
    expect(p.pct).toBe(2);
    expect(journalProgress([]).winRate).toBeNull();
  });
});

describe('engineSummary', () => {
  const now = new Date('2026-10-05T14:00:00Z');
  const base = {
    mode: 'paper' as const, session: 'open' as const, feed_ok: true, halt_reason: null, stops: 'held by the bot',
    calendar_configured: true, blocked_today: null, enabled: ['A'],
    risk: { per_trade_pct: 1, breaker_level: 0, live_disabled: false, daily_max_loss_pct: 2 }, day: null, position: null,
  };
  const row = (over = {}, ago = 5) => ({ state: { ...base, ...over }, updated_at: new Date(now.getTime() - ago * 1000).toISOString() });

  it('reports each condition, most serious first', () => {
    expect(engineSummary(null, now, 'A').level).toBe('warn');
    expect(engineSummary(row({}, 300), now, 'A')).toMatchObject({ level: 'bad', text: expect.stringMatching(/offline/) });
    expect(engineSummary(row({ halt_reason: 'order rejected' }), now, 'A').level).toBe('bad');
    expect(engineSummary(row({ data_error: 'unsupported market' }), now, 'A')).toMatchObject({ level: 'bad', text: expect.stringMatching(/Cannot get market data: unsupported market/) });
    expect(engineSummary(row({ session: 'closed' }), now, 'A').text).toMatch(/Market closed/);
    expect(engineSummary(row({ feed_ok: false }), now, 'A').text).toMatch(/stale/);
    expect(engineSummary(row({ blocked_today: 'fomc_day' }), now, 'A').text).toMatch(/fomc day/);
    expect(engineSummary(row(), now, 'B').level).toBe('warn'); // B is not enabled
    expect(engineSummary(row(), now, 'A')).toEqual({ text: 'Engine running on the simulated account.', level: 'ok' });
  });
});

describe('markets', () => {
  it('keeps each market budget in its own pool and currency', () => {
    const configs = [cfg('A', 400_000, 'US'), cfg('A', 30_000, 'MY'), cfg('B', 50_000, 'MY')];
    expect(allocation(configs, 1_000_000, 'B', 'US').allocated).toBe(400_000);
    expect(allocation(configs, 100_000, 'B', 'MY')).toEqual({ allocated: 30_000, remaining: 70_000 });
    expect(allocation(configs, 100_000, undefined, 'SG').allocated).toBe(0);
  });

  it('limits SG/MY budgets to the paper balance and US to the bot-reported equity', () => {
    const markets = [{ user_id: 'u', market: 'MY' as const, enabled: true, symbols: [], paper_balance: 80_000, updated_at: '' }];
    expect(accountLimit('US', 1_000_000, markets)).toBe(1_000_000);
    expect(accountLimit('US', null, markets)).toBeNull();
    expect(accountLimit('MY', 1_000_000, markets)).toBe(80_000);   // the US equity is irrelevant to Malaysia
    expect(accountLimit('SG', 1_000_000, markets)).toBeNull();      // no paper balance set yet
  });

  it('validates a local-market budget against the right pool and explains a missing balance', () => {
    const configs = [cfg('A', 30_000, 'MY')];
    expect(validateBudget('50000', 'B', configs, 80_000, 'MY').error).toBeNull();
    expect(validateBudget('50001', 'B', configs, 80_000, 'MY').error).toMatch(/MYR/);
    expect(validateBudget('100', 'B', configs, null, 'SG').error).toMatch(/SG paper balance/);
    expect(validateBudget('100', 'B', [], null, 'US').error).toMatch(/Start the bot/);
  });

  it('checks symbols against the market', () => {
    expect(parseSymbols('1155, 5225', 'MY')).toEqual({ symbols: ['1155', '5225'], error: null });
    expect(parseSymbols('d05 es3', 'SG').symbols).toEqual(['D05', 'ES3']);
    expect(parseSymbols('1155', 'US').error).toMatch(/Not valid US/);
    expect(parseSymbols('', 'MY').error).toMatch(/1 to 10/);
    expect(parseTickers('SPY', 'US').error).toBeNull();
  });

  it('describes a switched-off market and a local simulated engine', () => {
    const now = new Date('2026-10-05T14:00:00Z');
    const state = {
      mode: 'paper' as const, market: 'MY' as const, currency: 'MYR', session: 'open' as const, feed_ok: true, halt_reason: null,
      stops: 'held by the bot', calendar_configured: true, blocked_today: null, enabled: ['A'],
      risk: { per_trade_pct: 1, breaker_level: 0, live_disabled: false, daily_max_loss_pct: 2 }, day: null, position: null,
    };
    const row = { market: 'MY' as const, state, updated_at: new Date(now.getTime() - 5000).toISOString() };
    expect(engineSummary(null, now, 'A', false).text).toMatch(/switched off/);
    expect(engineSummary(row, now, 'A', true).text).toMatch(/simulated MYR account/);
    expect(engineSummary(row, now, 'A', false).text).toMatch(/no new entries/);
  });
});

describe('funnelRows', () => {
  it('orders the strategy stages before the engine gates, hides zeros, and marks losses', () => {
    const rows = funnelRows({
      A: { window_minutes: 5000, regime_failed_close_above_vwap: 2500, regime_failed_vwap_rising: 0, regime_minutes: 300, signals: 4 },
      engine: { signals_seen: 4, 'blocked: daily loss limit reached': 1, skipped_shares: 2, entries_filled: 1 },
    }, 'A');
    expect(rows.map((r) => r.key)).toEqual([
      'A.window_minutes', 'A.regime_failed_close_above_vwap', 'A.regime_minutes', 'A.signals',
      'engine.signals_seen', 'engine.blocked: daily loss limit reached', 'engine.skipped_shares', 'engine.entries_filled',
    ]);
    const lost = rows.filter((r) => r.lost).map((r) => r.label);
    expect(lost).toEqual(['Price below VWAP', 'Blocked by risk rule: daily loss limit reached', 'Budget too small for even one share']);
    expect(rows.find((r) => r.key === 'A.window_minutes')?.count).toBe(5000);
  });

  it('copes with older results that have no funnel and unknown counters', () => {
    expect(funnelRows(undefined, 'B')).toEqual([]);
    expect(funnelRows({ B: { some_new_counter: 3 } }, 'B')[0]).toMatchObject({ label: 'some new counter', lost: false });
  });
});

describe('trade limits', () => {
  it('shows cents for small amounts and whole units for large ones', () => {
    expect(money(0.15, 'USD')).toMatch(/0\.15/);
    expect(money(12.5, 'USD')).toMatch(/12\.50/);
    expect(money(1500, 'USD')).not.toMatch(/\./);
  });

  it('reads and writes limits without disturbing other params', () => {
    expect(limitsOf(undefined)).toEqual({});
    expect(limitsOf({ params: { shared: { max_trade_notional: '100', allow_fractional: true, junk: 1 } } as never })).toEqual({
      max_trade_notional: 100, allow_fractional: true,
    });
    const a = withLimits({ t1_r: 2 }, { max_trade_notional: 100 });
    expect(a).toEqual({ t1_r: 2, shared: { max_trade_notional: 100 } });
    const b = withLimits(a, { allow_fractional: true });
    expect(b).toEqual({ t1_r: 2, shared: { max_trade_notional: 100, allow_fractional: true } });
    expect(withLimits(b, { max_trade_notional: 0, allow_fractional: false })).toEqual({ t1_r: 2 });   // cleared: back to defaults
  });

  it('validates the per-trade cap', () => {
    expect(parseMaxTrade('', 1000)).toEqual({ value: 0, error: null });
    expect(parseMaxTrade('100', 1000)).toEqual({ value: 100, error: null });
    expect(parseMaxTrade('1,000', 1000).error).toBeNull();
    expect(parseMaxTrade('1001', 1000).error).toMatch(/larger than the budget/);
    expect(parseMaxTrade('abc', 1000).error).toMatch(/Enter an amount/);
    expect(parseMaxTrade('0', 1000).error).toMatch(/Enter an amount/);
  });

  it('describes the limits in words', () => {
    expect(limitsText({}, 'USD')).toBe('');
    expect(limitsText({ max_trade_notional: 100, allow_fractional: true }, 'USD')).toMatch(/at most \$100\.00 per trade, fractional shares allowed/);
  });
});
