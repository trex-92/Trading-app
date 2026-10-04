-- Live state of the strategy engine (written by the bot, shown on the strategy tabs).
create table public.engine_status (
  user_id uuid primary key references auth.users(id) on delete cascade,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.engine_status enable row level security;
create policy "own rows" on public.engine_status for select using (user_id = auth.uid());
alter publication supabase_realtime add table public.engine_status;
