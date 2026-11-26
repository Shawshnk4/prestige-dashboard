-- PRESTIGE tables. The server also creates them on start; run this in Supabase SQL editor if you prefer.
-- Row-level security is on with no policies: only the server (database owner connection) can read or write.
create table if not exists fyers_daily (symbol text not null, d date not null, close double precision not null,
  fetched_at timestamptz not null default now(), primary key (symbol, d));
create table if not exists runs (id bigserial primary key, created_at timestamptz not null default now(), created_by text,
  data_end date, result jsonb);
create table if not exists portfolios (id bigserial primary key, created_at timestamptz not null default now(), created_by text,
  name text, schedule text, status text not null default 'current', capital double precision, hedge double precision,
  trade_date date, signal_date date, contract date, positions jsonb, closed_at timestamptz, closed_by text, exit_prices jsonb, note text);
create table if not exists app_state (key text primary key, value text, updated_at timestamptz not null default now());
alter table fyers_daily enable row level security;
alter table runs enable row level security;
alter table portfolios enable row level security;
alter table app_state enable row level security;
