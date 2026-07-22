-- SharpLine website schema — run once in Supabase SQL editor.

create table if not exists public.alerts (
  key          text primary key,          -- event|market|selection|book
  sport        text not null,
  event        text not null,
  commence     timestamptz not null,
  market       text not null,
  market_class text not null default 'game_line',  -- game_line | prop
  selection    text not null,
  book         text not null,
  book_category text not null default 'sportsbook', -- sportsbook | exchange | dfs
  odds         numeric not null,          -- decimal odds at alert time
  fair_prob    numeric not null,
  fair_odds    numeric not null,
  ev_pct       numeric not null,
  stake_units  numeric,
  anchor       text,
  deeplink     text,
  depth_note   text,
  alerted_at   timestamptz not null default now(),
  result       text,                      -- win | loss | push | void (null = pending)
  clv_pct      numeric,
  graded_at    timestamptz
);

create index if not exists alerts_alerted_at_idx on public.alerts (alerted_at desc);
create index if not exists alerts_book_category_idx on public.alerts (book_category, alerted_at desc);
create index if not exists alerts_commence_idx on public.alerts (commence);

-- Public read (site is public at launch), writes only via service key.
alter table public.alerts enable row level security;

drop policy if exists "public read" on public.alerts;
create policy "public read" on public.alerts
  for select using (true);

-- Realtime feed for the site
alter publication supabase_realtime add table public.alerts;
