import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import { fetchEvents, fetchOrders, fetchPositions, fetchStatus, sendCommand, subscribeToBot } from '@/lib/providers/bot';
import type { BotEvent, BotStatus, Order, Position } from './types';

type BotState = {
  status: BotStatus | null;
  positions: Position[];
  orders: Order[];
  events: BotEvent[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  setHalted: (halt: boolean) => Promise<void>;
};

const BotContext = createContext<BotState | null>(null);
const FALLBACK_POLL_MS = 15_000;

export function BotProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const [s, p, o, e] = await Promise.all([fetchStatus(), fetchPositions(), fetchOrders(), fetchEvents()]);
      setStatus(s);
      setPositions(p);
      setOrders(o);
      setEvents(e);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const unsubscribe = subscribeToBot(refresh);
    const timer = setInterval(refresh, FALLBACK_POLL_MS); // covers dropped realtime sockets
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [refresh]);

  const setHalted = useCallback(
    async (halt: boolean) => {
      await sendCommand(halt ? 'halt' : 'resume');
      setStatus((s) => (s ? { ...s, halted: halt } : s)); // optimistic; bot confirms via snapshot
    },
    [],
  );

  return (
    <BotContext.Provider value={{ status, positions, orders, events, loading, error, refresh, setHalted }}>
      {children}
    </BotContext.Provider>
  );
}

export function useBot(): BotState {
  const ctx = useContext(BotContext);
  if (!ctx) throw new Error('useBot must be used inside BotProvider');
  return ctx;
}
