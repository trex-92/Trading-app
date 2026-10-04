import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import {
  deleteBacktest, fetchBacktests, fetchConfigs, fetchEngines, fetchJournal, fetchMarkets, queueBacktest, saveConfig, saveMarket,
  subscribeToStrategies,
} from '@/lib/providers/strategies';
import { withLimits, type SharedLimits } from './calculations';
import type { BacktestRun, EngineRow, JournalTrade, MarketCode, MarketConfig, StrategyCode, StrategyConfig } from './types';

type State = {
  configs: StrategyConfig[];
  runs: BacktestRun[];
  journal: JournalTrade[];
  markets: MarketConfig[];
  engines: Partial<Record<MarketCode, EngineRow>>;
  error: string | null;
  refresh: () => Promise<void>;
  saveBudget: (s: StrategyCode, market: MarketCode, budget: number) => Promise<void>;
  setEnabled: (s: StrategyCode, market: MarketCode, enabled: boolean) => Promise<void>;
  saveLimits: (s: StrategyCode, market: MarketCode, change: SharedLimits) => Promise<void>;
  saveMarketConfig: (market: MarketCode, fields: Partial<Pick<MarketConfig, 'enabled' | 'symbols' | 'paper_balance'>>) => Promise<void>;
  runBacktest: (s: StrategyCode, params: BacktestRun['params']) => Promise<void>;
  removeRun: (id: string) => Promise<void>;
};

const Ctx = createContext<State | null>(null);
const POLL_MS = 10_000;

export function StrategiesProvider({ children }: { children: ReactNode }) {
  const [configs, setConfigs] = useState<StrategyConfig[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [journal, setJournal] = useState<JournalTrade[]>([]);
  const [markets, setMarkets] = useState<MarketConfig[]>([]);
  const [engines, setEngines] = useState<Partial<Record<MarketCode, EngineRow>>>({});
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const [c, r, j, e, m] = await Promise.all([fetchConfigs(), fetchBacktests(), fetchJournal(), fetchEngines(), fetchMarkets()]);
      setConfigs(c);
      setRuns(r);
      setJournal(j);
      setEngines(Object.fromEntries(e.map((row) => [row.market ?? 'US', row])));
      setMarkets(m);
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

  const saveBudget = useCallback(async (s: StrategyCode, market: MarketCode, budget: number) => {
    await saveConfig(s, market, { budget });
    await refresh();
  }, [refresh]);

  const setEnabled = useCallback(async (s: StrategyCode, market: MarketCode, enabled: boolean) => {
    await saveConfig(s, market, { enabled });
    await refresh();
  }, [refresh]);

  const saveLimits = useCallback(async (s: StrategyCode, market: MarketCode, change: SharedLimits) => {
    const current = configs.find((c) => c.strategy === s && (c.market ?? 'US') === market);
    await saveConfig(s, market, { params: withLimits(current?.params, change) });
    await refresh();
  }, [configs, refresh]);

  const saveMarketConfig = useCallback(async (market: MarketCode, fields: Parameters<State['saveMarketConfig']>[1]) => {
    await saveMarket(market, fields);
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
    <Ctx.Provider value={{ configs, runs, journal, markets, engines, error, refresh, saveBudget, setEnabled, saveLimits, saveMarketConfig, runBacktest, removeRun }}>
      {children}
    </Ctx.Provider>
  );
}

export function useStrategies(): State {
  const v = useContext(Ctx);
  if (!v) throw new Error('useStrategies must be used inside StrategiesProvider');
  return v;
}
