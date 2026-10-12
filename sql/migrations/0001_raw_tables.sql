-- Raw ingest tables. Rows are upserted on every ingest run:
--   ingested_at = when the row was first seen
--   updated_at  = when its value last changed (a revision)

create table raw_eia (
  respondent  text        not null,
  type        text        not null,  -- D = actual demand, DF = operator day-ahead forecast
  period_utc  timestamptz not null,  -- hour-ending: 05:00 covers 04:00-05:00
  value       numeric,               -- MWh; null when EIA publishes an empty value
  ingested_at timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (respondent, type, period_utc)
);

create table raw_weather (
  location       text        not null,
  valid_time_utc timestamptz not null,
  lead_days      int         not null,  -- forecast made this many days before valid time
  model          text        not null,
  variable       text        not null,
  value          numeric     not null,
  ingested_at    timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  primary key (location, valid_time_utc, lead_days, model, variable)
);

-- Supabase serves the public schema over its REST API. With RLS on and no policies the
-- anon key can read nothing here; the dashboard will get dedicated read-only views.
alter table raw_eia enable row level security;
alter table raw_weather enable row level security;
