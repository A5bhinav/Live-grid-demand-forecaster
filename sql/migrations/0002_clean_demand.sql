-- Screened actual demand, one row per hour from the first raw hour to the last.
-- Rebuilt from raw_eia by the clean job; flags near the latest hour can change as later
-- data arrives.

create table clean_demand (
  period_utc timestamptz primary key,  -- hour-ending
  demand_mw  numeric,                  -- reported value, or the filled-in one when imputed
  flag       text,                     -- null when the hour passed every screen
  imputed    boolean     not null default false,
  updated_at timestamptz not null default now()
);

alter table clean_demand enable row level security;
