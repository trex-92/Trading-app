import {
  allocation, defaultRange, downsample, equityBars, isIsoDate, journalProgress, parseTickers, validateBudget, validateRange,
} from './calculations';
import type { JournalTrade, StrategyConfig } from './types';

const cfg = (strategy: 'A' | 'B' | 'C', budget: number): StrategyConfig => ({
  user_id: 'u', strategy, enabled: false, budget, params: {}, updated_at: '',
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
    expect(validateRange('2026-01-01', '2026-09-30', today)).toMatch(/Maximum/);
    expect(validateRange('2026-09-01', '2027-01-01', today)).toMatch(/future/);
    expect(validateRange('x', '2026-09-30', today)).toMatch(/Dates/);
  });

  it('defaults to the last 30 days', () => {
    expect(defaultRange(new Date('2026-10-05T12:00:00Z'))).toEqual({ start: '2026-09-05', end: '2026-10-05' });
  });

  it('parses tickers', () => {
    expect(parseTickers('spy, qqq').tickers).toEqual(['SPY', 'QQQ']);
    expect(parseTickers('').error).toMatch(/1 to 3/);
    expect(parseTickers('a b c d').error).toMatch(/1 to 3/);
    expect(parseTickers('SPY; DROP').error).toMatch(/letters/);
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
