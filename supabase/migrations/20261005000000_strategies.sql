-- Strategy tabs: per-strategy settings + budget, backtest requests/results, and the paper/live trade journal.
-- The app writes strategy_configs and queues backtest_runs; the bot worker (service role) executes and fills results.

create table public.strategy_configs (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  strategy text not null check (strategy in ('A','B','C')),
  enabled boolean not null default false,
  budget numeric not null default 0 check (budget >= 0),
  params jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, strategy)
);

-- The budgets of all strategies together may not exceed the account's net asset value reported by the bot.
create or replace function public.check_strategy_budget() returns trigger language plpgsql as $$
declare
  acct_equity numeric;
  others numeric;
begin
  new.updated_at := now();
  if tg_op = 'UPDATE' and new.budget is not distinct from old.budget then
    return new;  -- toggling "enabled" must keep working if the balance has since dipped
  end if;
  if new.budget = 0 then
    return new;
  end if;
  select equity into acct_equity from public.bot_status where user_id = new.user_id;
  if acct_equity is null then
    raise exception 'Account balance unknown yet: start the bot so it can report it' using errcode = 'check_violation';
  end if;
  select coalesce(sum(budget), 0) into others
    from public.strategy_configs where user_id = new.user_id and strategy <> new.strategy;
  if others + new.budget > acct_equity then
    raise exception 'Total budget % would exceed the account balance % (other strategies already use %)',
      others + new.budget, acct_equity, others using errcode = 'check_violation';
  end if;
  return new;
end $$;

create trigger strategy_budget before insert or update on public.strategy_configs
  for each row execute function public.check_strategy_budget();

create table public.backtest_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  strategy text not null check (strategy in ('A','B','C')),
  params jsonb not null default '{}'::jsonb,   -- {tickers, start, end, budget, overrides}
  status text not null default 'pending' check (status in ('pending','running','done','error')),
  summary jsonb,                               -- small: stats + notes (lists load this)
  result jsonb,                                -- large: trades, equity curve, data coverage
  error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
create index backtest_runs_user_created on public.backtest_runs(user_id, created_at desc);

-- Journal of paper/live trades (written by the bot; the strategy engine for these comes in the next phase).
create table public.strategy_trades (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  strategy text not null check (strategy in ('A','B','C')),
  mode text not null check (mode in ('paper','live')),
  ticker text not null,
  entered_at timestamptz not null,
  exited_at timestamptz,
  entry numeric, stop numeric, shares numeric, costs numeric, pnl numeric, r_multiple numeric,
  exits jsonb, regime jsonb
);
create index strategy_trades_user on public.strategy_trades(user_id, strategy, entered_at desc);

alter table public.strategy_configs enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.strategy_trades enable row level security;

create policy "own rows" on public.strategy_configs for select using (user_id = auth.uid());
create policy "write own" on public.strategy_configs for insert with check (user_id = auth.uid());
create policy "update own" on public.strategy_configs for update using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "own rows" on public.backtest_runs for select using (user_id = auth.uid());
create policy "queue own" on public.backtest_runs for insert
  with check (user_id = auth.uid() and status = 'pending' and result is null and summary is null);
create policy "delete own" on public.backtest_runs for delete using (user_id = auth.uid());

create policy "own rows" on public.strategy_trades for select using (user_id = auth.uid());

alter publication supabase_realtime add table public.strategy_configs, public.backtest_runs, public.strategy_trades;
