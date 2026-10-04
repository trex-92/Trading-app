import type { Order, Position } from './types';

/** Supabase returns numerics as numbers or strings depending on column type; normalise. */
export const num = (v: number | string | null | undefined): number => (v == null ? 0 : Number(v));

export function positionPnl(p: Position): number {
  return (num(p.last_price) - num(p.avg_price)) * num(p.qty);
}

export function positionPnlPct(p: Position): number {
  const cost = num(p.avg_price) * num(p.qty);
  return cost === 0 ? 0 : (positionPnl(p) / cost) * 100;
}

export function portfolioMetrics(positions: Position[]) {
  let marketValue = 0;
  let cost = 0;
  for (const p of positions) {
    marketValue += num(p.last_price) * num(p.qty);
    cost += num(p.avg_price) * num(p.qty);
  }
  const unrealized = marketValue - cost;
  return { marketValue, cost, unrealized, unrealizedPct: cost === 0 ? 0 : (unrealized / cost) * 100 };
}

/** The bot pushes a snapshot every tick; no update for 3x the expected interval means it is down. */
export function isStale(updatedAt: string | undefined, now: Date, maxAgeSeconds = 180): boolean {
  if (!updatedAt) return true;
  return (now.getTime() - new Date(updatedAt).getTime()) / 1000 > maxAgeSeconds;
}

/** Local calendar day (YYYY-MM-DD), not the UTC date in the ISO timestamp. */
export function localDay(iso: string): string {
  const d = new Date(iso);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

export function orderSummary(orders: Order[], today: string) {
  const todays = orders.filter((o) => localDay(o.created_at) === today);
  const count = (s: Order['status'][]) => todays.filter((o) => s.includes(o.status)).length;
  return { total: todays.length, filled: count(['FILLED']), blocked: count(['BLOCKED', 'REJECTED']) };
}
