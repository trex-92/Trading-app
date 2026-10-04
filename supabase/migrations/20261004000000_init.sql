-- Trading bot monitor schema. The Python worker writes with the service-role key (bypasses RLS);
-- the app reads own rows and may only insert bot_commands.

create table public.bot_status (
  user_id uuid primary key references auth.users(id) on delete cascade,
  equity numeric not null default 0,
  cash numeric not null default 0,
  day_pnl numeric not null default 0,
  halted boolean not null default false,
  broker text not null default '',
  env text not null default '',
  updated_at timestamptz not null default now()
);

create table public.positions (
  user_id uuid not null references auth.users(id) on delete cascade,
  symbol text not null,
  qty numeric not null,
  avg_price numeric not null,
  last_price numeric not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, symbol)
);

create table public.orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  symbol text not null,
  side text not null check (side in ('BUY','SELL')),
  qty numeric not null,
  price numeric,
  status text not null,
  reason text not null default '',
  broker_order_id text not null default '',
  created_at timestamptz not null default now()
);
create index orders_user_created on public.orders(user_id, created_at desc);

create table public.bot_events (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  level text not null check (level in ('INFO','WARN','ERROR')),
  msg text not null,
  created_at timestamptz not null default now()
);
create index bot_events_user_created on public.bot_events(user_id, created_at desc);

-- App -> bot control channel. The worker claims pending rows and marks them done.
create table public.bot_commands (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  command text not null check (command in ('halt','resume')),
  status text not null default 'pending' check (status in ('pending','done')),
  created_at timestamptz not null default now()
);

alter table public.bot_status enable row level security;
alter table public.positions enable row level security;
alter table public.orders enable row level security;
alter table public.bot_events enable row level security;
alter table public.bot_commands enable row level security;

create policy "own rows" on public.bot_status for select using (user_id = auth.uid());
create policy "own rows" on public.positions for select using (user_id = auth.uid());
create policy "own rows" on public.orders for select using (user_id = auth.uid());
create policy "own rows" on public.bot_events for select using (user_id = auth.uid());
create policy "own rows" on public.bot_commands for select using (user_id = auth.uid());
create policy "send commands" on public.bot_commands for insert with check (user_id = auth.uid());

alter publication supabase_realtime add table public.bot_status, public.positions, public.orders, public.bot_events;
