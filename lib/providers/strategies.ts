import { supabase } from '@/lib/supabase';
import type {
  BacktestResult, BacktestRun, EngineRow, JournalTrade, MarketCode, MarketConfig, StrategyCode, StrategyConfig,
} from '@/features/strategies/types';

function check<T>(res: { data: T | null; error: { message: string } | null }): T | null {
  if (res.error) throw new Error(res.error.message);
  return res.data;
}

async function userId(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  if (!data.session) throw new Error('Not signed in');
  return data.session.user.id;
}

export async function fetchConfigs(): Promise<StrategyConfig[]> {
  return check(await supabase.from('strategy_configs').select('*').returns<StrategyConfig[]>()) ?? [];
}

/** Upserts only the given fields. The database trigger rejects budgets above the account balance. */
export async function saveConfig(
  strategy: StrategyCode,
  market: MarketCode,
  fields: Partial<Pick<StrategyConfig, 'budget' | 'enabled' | 'params'>>,
): Promise<void> {
  const user_id = await userId();
  const { error } = await supabase
    .from('strategy_configs')
    .upsert({ user_id, strategy, market, ...fields }, { onConflict: 'user_id,strategy,market' });
  if (error) throw new Error(error.message);
}

const RUN_COLUMNS = 'id,strategy,params,status,summary,error,created_at,finished_at';

export async function fetchBacktests(): Promise<BacktestRun[]> {
  return (
    check(
      await supabase.from('backtest_runs').select(RUN_COLUMNS).order('created_at', { ascending: false }).limit(30)
        .returns<BacktestRun[]>(),
    ) ?? []
  );
}

export async function fetchBacktestResult(id: string): Promise<BacktestResult | null> {
  const row = check(await supabase.from('backtest_runs').select('result').eq('id', id).maybeSingle<{ result: BacktestResult | null }>());
  return row?.result ?? null;
}

export async function queueBacktest(strategy: StrategyCode, params: BacktestRun['params']): Promise<void> {
  const { error } = await supabase.from('backtest_runs').insert({ strategy, params, user_id: await userId() });
  if (error) throw new Error(error.message);
}

export async function deleteBacktest(id: string): Promise<void> {
  const { error } = await supabase.from('backtest_runs').delete().eq('id', id);
  if (error) throw new Error(error.message);
}

export async function fetchJournal(limit = 200): Promise<JournalTrade[]> {
  return (
    check(
      await supabase.from('strategy_trades').select('id,strategy,market,mode,ticker,entered_at,exited_at,entry,shares,pnl,r_multiple')
        .order('entered_at', { ascending: false }).limit(limit).returns<JournalTrade[]>(),
    ) ?? []
  );
}

export async function fetchEngines(): Promise<EngineRow[]> {
  return check(await supabase.from('engine_status').select('market,state,updated_at').returns<EngineRow[]>()) ?? [];
}

export async function fetchMarkets(): Promise<MarketConfig[]> {
  return check(await supabase.from('market_configs').select('*').returns<MarketConfig[]>()) ?? [];
}

/** Upserts only the given fields (which markets run, their symbols, and the SG/MY paper balance). */
export async function saveMarket(
  market: MarketCode,
  fields: Partial<Pick<MarketConfig, 'enabled' | 'symbols' | 'paper_balance'>>,
): Promise<void> {
  const user_id = await userId();
  const { error } = await supabase.from('market_configs').upsert({ user_id, market, ...fields }, { onConflict: 'user_id,market' });
  if (error) throw new Error(error.message);
}

export function subscribeToStrategies(onChange: () => void): () => void {
  const channel = supabase.channel('strategies');
  for (const table of ['strategy_configs', 'backtest_runs', 'strategy_trades', 'engine_status', 'market_configs']) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table }, onChange);
  }
  channel.subscribe();
  return () => {
    supabase.removeChannel(channel);
  };
}
