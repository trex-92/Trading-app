import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import {
  deleteBacktest, fetchBacktests, fetchConfigs, fetchJournal, queueBacktest, saveConfig, subscribeToStrategies,
} from '@/lib/providers/strategies';
import type { BacktestRun, JournalTrade, StrategyCode, StrategyConfig } from './types';

type State = {
  configs: StrategyConfig[];
  runs: BacktestRun[];
  journal: JournalTrade[];
  error: string | null;
  refresh: () => Promise<void>;
  saveBudget: (s: StrategyCode, budget: number) => Promise<void>;
  setEnabled: (s: StrategyCode, enabled: boolean) => Promise<void>;
  runBacktest: (s: StrategyCode, params: BacktestRun['params']) => Promise<void>;
  removeRun: (id: string) => Promise<void>;
};

const Ctx = createContext<State | null>(null);
const POLL_MS = 10_000;

export function StrategiesProvider({ children }: { children: ReactNode }) {
  const [configs, setConfigs] = useState<StrategyConfig[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [journal, setJournal] = useState<JournalTrade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const [c, r, j] = await Promise.all([fetchConfigs(), fetchBacktests(), fetchJournal()]);
      setConfigs(c);
      setRuns(r);
      setJournal(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    const unsubscribe = subscribeToStrategies(refresh);
    const timer = setInterval(refresh, POLL_MS);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [refresh]);

  const saveBudget = useCallback(async (s: StrategyCode, budget: number) => {
    await saveConfig(s, { budget });
    await refresh();
  }, [refresh]);

  const setEnabled = useCallback(async (s: StrategyCode, enabled: boolean) => {
    await saveConfig(s, { enabled });
    await refresh();
  }, [refresh]);

  const runBacktest = useCallback(async (s: StrategyCode, params: BacktestRun['params']) => {
    await queueBacktest(s, params);
    await refresh();
  }, [refresh]);

  const removeRun = useCallback(async (id: string) => {
    await deleteBacktest(id);
    await refresh();
  }, [refresh]);

  return (
    <Ctx.Provider value={{ configs, runs, journal, error, refresh, saveBudget, setEnabled, runBacktest, removeRun }}>
      {children}
    </Ctx.Provider>
  );
}

export function useStrategies(): State {
  const v = useContext(Ctx);
  if (!v) throw new Error('useStrategies must be used inside StrategiesProvider');
  return v;
}
