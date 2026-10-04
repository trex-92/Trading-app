import { isStale, localDay, orderSummary, portfolioMetrics, positionPnlPct } from './calculations';
import type { Order } from './types';

const pos = (symbol: string, qty: number, avg: number, last: number) => ({
  symbol,
  qty,
  avg_price: avg,
  last_price: last,
});

const order = (status: Order['status'], created_at: string): Order => ({
  id: created_at + status,
  symbol: 'AAPL',
  side: 'BUY',
  qty: 1,
  price: 1,
  status,
  reason: '',
  created_at,
});

describe('portfolioMetrics', () => {
  it('sums value and unrealized P&L', () => {
    const m = portfolioMetrics([pos('A', 10, 100, 110), pos('B', 5, 20, 10)]);
    expect(m.marketValue).toBe(1150);
    expect(m.cost).toBe(1100);
    expect(m.unrealized).toBe(50);
    expect(m.unrealizedPct).toBeCloseTo(4.545, 2);
  });

  it('handles empty and zero-cost portfolios', () => {
    expect(portfolioMetrics([]).unrealizedPct).toBe(0);
    expect(positionPnlPct(pos('A', 0, 0, 5))).toBe(0);
  });

  it('accepts numeric strings from Postgres', () => {
    expect(portfolioMetrics([{ symbol: 'A', qty: '2', avg_price: '10', last_price: '12' } as never]).unrealized).toBe(4);
  });
});

describe('isStale', () => {
  const now = new Date('2026-10-04T12:00:00Z');
  it('flags missing and old snapshots', () => {
    expect(isStale(undefined, now)).toBe(true);
    expect(isStale('2026-10-04T11:50:00Z', now)).toBe(true);
    expect(isStale('2026-10-04T11:59:00Z', now)).toBe(false);
  });
});

describe('orderSummary', () => {
  it('buckets by local calendar day, not UTC date', () => {
    const iso = new Date(2026, 9, 4, 0, 30).toISOString(); // 00:30 local; UTC date may differ
    const today = localDay(iso);
    const yesterday = localDay(new Date(2026, 9, 3, 23, 30).toISOString());
    const s = orderSummary([order('FILLED', iso), order('BLOCKED', iso), order('FILLED', new Date(2026, 9, 3, 23, 30).toISOString())], today);
    expect(yesterday).not.toBe(today);
    expect(s).toEqual({ total: 2, filled: 1, blocked: 1 });
  });
});
