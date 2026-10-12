-- Weather in wide format: one row per location and hour instead of one per variable.
-- That is a seventh of the rows, which matters on Supabase's 500 MB free tier.
-- Existing rows are carried over.

create table raw_weather_wide (
  location             text        not null,
  valid_time_utc       timestamptz not null,
  lead_days            int         not null,  -- forecast made this many days before valid time
  model                text        not null,
  temperature_2m       numeric,  -- deg C
  dew_point_2m         numeric,  -- deg C
  relative_humidity_2m numeric,  -- %
  cloud_cover          numeric,  -- %
  shortwave_radiation  numeric,  -- W/m2, mean over the preceding hour
  wind_speed_10m       numeric,  -- km/h
  precipitation        numeric,  -- mm, sum over the preceding hour
  ingested_at          timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  primary key (location, valid_time_utc, lead_days, model)
);

insert into raw_weather_wide
select
  location, valid_time_utc, lead_days, model,
  max(value) filter (where variable = 'temperature_2m'),
  max(value) filter (where variable = 'dew_point_2m'),
  max(value) filter (where variable = 'relative_humidity_2m'),
  max(value) filter (where variable = 'cloud_cover'),
  max(value) filter (where variable = 'shortwave_radiation'),
  max(value) filter (where variable = 'wind_speed_10m'),
  max(value) filter (where variable = 'precipitation'),
  min(ingested_at),
  max(updated_at)
from raw_weather
group by location, valid_time_utc, lead_days, model;

drop table raw_weather;
alter table raw_weather_wide rename to raw_weather;
alter table raw_weather rename constraint raw_weather_wide_pkey to raw_weather_pkey;
alter table raw_weather enable row level security;
