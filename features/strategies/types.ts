export type StrategyCode = 'A' | 'B' | 'C';
export type MarketCode = 'US' | 'SG' | 'MY';

export type MarketConfig = {
  user_id: string;
  market: MarketCode;
  enabled: boolean;
  symbols: string[];
  paper_balance: number;
  updated_at: string;
};

export type StrategyConfig = {
  user_id: string;
  strategy: StrategyCode;
  market: MarketCode;
  enabled: boolean;
  budget: number;
  params: Record<string, unknown>;
  updated_at: string;
};

export type BacktestStats = {
  n_trades: number;
  start_equity: number;
  total_pnl?: number;
  return_pct?: number;
  win_rate_pct?: number;
  avg_r?: number;
  expectancy_r_95ci?: [number, number] | null;
  profit_factor?: number | null;
  avg_win_r?: number | null;
  avg_loss_r?: number | null;
  max_drawdown_pct?: number;
  days_traded?: number;
  avg_minutes_held?: number;
  total_costs?: number;
  note?: string;
};

export type BacktestRequest = { market?: MarketCode; tickers: string[]; start: string; end: string; budget: number };

export type BacktestSummary = {
  stats: BacktestStats;
  notes: string[];
  request: BacktestRequest;
  trades_total: number;
};

export type BacktestRun = {
  id: string;
  strategy: StrategyCode;
  params: Partial<BacktestRequest> & { overrides?: Record<string, unknown> };
  status: 'pending' | 'running' | 'done' | 'error';
  summary: BacktestSummary | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type BacktestTrade = {
  date: string;
  ticker: string;
  strategy: StrategyCode;
  entry_ts: string;
  entry: number;
  stop: number;
  shares: number;
  exits: { ts: string; qty: number; price: number; reason: string }[];
  costs: number;
  pnl: number;
  r: number;
  minutes_held: number;
};

export type BacktestResult = {
  trades: BacktestTrade[];
  equity_curve: { ts: string; equity: number }[];
  data: Record<string, { bars: number; days: number; first: string | null; last: string | null; has_premarket: boolean }>;
  skipped: Record<string, number>;
  funnel?: Record<string, Record<string, number>>;
};

export type JournalTrade = {
  id: string;
  strategy: StrategyCode;
  market?: MarketCode;
  mode: 'paper' | 'live';
  ticker: string;
  entered_at: string;
  exited_at: string | null;
  entry: number | null;
  shares: number | null;
  pnl: number | null;
  r_multiple: number | null;
};

export type EngineState = {
  mode: 'paper';
  market?: MarketCode;
  currency?: string;
  simulated_by?: string;
  session: 'open' | 'closed';
  feed_ok: boolean;
  halt_reason: string | null;
  data_error?: string | null;
  stops: string;
  calendar_configured: boolean;
  blocked_today: string | null;
  enabled: string[];
  risk: { per_trade_pct: number; breaker_level: number; live_disabled: boolean; daily_max_loss_pct: number };
  day: { trades: number; realized: number; consecutive_losses: number } | null;
  position: { strategy: StrategyCode; ticker: string; shares: number; entry: number; stop: number; t1: number } | null;
};

export type EngineRow = { market?: MarketCode; state: EngineState; updated_at: string };
