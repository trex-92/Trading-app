-- Choose which markets run (US, SG, MY), per-market budgets, and per-market engine status/journal.
create table public.market_configs (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  market text not null check (market in ('US','SG','MY')),
  enabled boolean not null default false,
  symbols text[] not null default '{}',
  paper_balance numeric not null default 0 check (paper_balance >= 0),  -- simulated cash for SG/MY (Moomoo has no simulator there)
  updated_at timestamptz not null default now(),
  primary key (user_id, market)
);
alter table public.market_configs enable row level security;
create policy "own rows" on public.market_configs for select using (user_id = auth.uid());
create policy "write own" on public.market_configs for insert with check (user_id = auth.uid());
create policy "update own" on public.market_configs for update using (user_id = auth.uid()) with check (user_id = auth.uid());
alter publication supabase_realtime add table public.market_configs;

-- One budget per (strategy, market), in that market's own currency.
alter table public.strategy_configs add column market text not null default 'US' check (market in ('US','SG','MY'));
alter table public.strategy_configs drop constraint strategy_configs_pkey;
alter table public.strategy_configs add primary key (user_id, strategy, market);

alter table public.strategy_trades add column market text not null default 'US' check (market in ('US','SG','MY'));

alter table public.engine_status add column market text not null default 'US' check (market in ('US','SG','MY'));
alter table public.engine_status drop constraint engine_status_pkey;
alter table public.engine_status add primary key (user_id, market);

-- Budgets of all strategies in one market may not exceed that market's balance:
-- US = the net asset value the bot reports from the Moomoo simulated account; SG/MY = the paper balance you set.
create or replace function public.check_strategy_budget() returns trigger language plpgsql as $$
declare
  acct numeric;
  others numeric;
begin
  new.updated_at := now();
  if tg_op = 'UPDATE' and new.budget is not distinct from old.budget and new.market = old.market then
    return new;  -- toggling "enabled" must keep working if the balance has since dipped
  end if;
  if new.budget = 0 then
    return new;
  end if;
  if new.market = 'US' then
    select equity into acct from public.bot_status where user_id = new.user_id;
  else
    select paper_balance into acct from public.market_configs where user_id = new.user_id and market = new.market;
  end if;
  if acct is null or acct <= 0 then
    raise exception 'No account balance known for %: start the bot (US) or set the paper balance in Settings (SG/MY)', new.market
      using errcode = 'check_violation';
  end if;
  select coalesce(sum(budget), 0) into others from public.strategy_configs
    where user_id = new.user_id and market = new.market and strategy <> new.strategy;
  if others + new.budget > acct then
    raise exception 'Total % budget % would exceed the account balance % (other strategies already use %)',
      new.market, others + new.budget, acct, others using errcode = 'check_violation';
  end if;
  return new;
end $$;
