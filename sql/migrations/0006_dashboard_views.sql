-- What the public dashboard may read. The tables stay closed to the publishable key
-- (row-level security with no policies); these views expose chosen columns and are owned
-- by the table owner, so they read through RLS on purpose. Supabase's advisor reports
-- such views as "security definer"; that is the design here.

create table backtest_results (
  run_at               timestamptz not null,
  first_month          text        not null,
  last_month           text        not null,
  model                text        not null,
  breakdown            text        not null,  -- overall, time_of_day, season or month
  key                  text        not null,  -- 'all', or the breakdown's value
  hours                int         not null,
  mape                 numeric     not null,
  mae_mw               numeric,
  rmse_mw              numeric,
  peak_abs_error_mw    numeric,
  peak_abs_hours_off   numeric,
  win_rate_vs_operator numeric,
  primary key (run_at, model, breakdown, key)
);
alter table backtest_results enable row level security;

-- The role behind Supabase's publishable key. It exists there already.
do $$
begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  execute format('grant usage on schema %I to anon', current_schema());
end $$;

-- Hour by hour for the last five weeks: what was issued, what the operator forecast,
-- and what demand turned out to be. Flagged actuals are withheld.
create view dashboard_hourly as
with live as (
  select distinct on (target_time_utc)
    target_time_utc as period_utc, yhat_mw, model_version, issued_at
  from forecasts
  order by target_time_utc, issued_at
),
hours as (
  select period_utc from clean_demand where period_utc >= now() - interval '35 days'
  union
  select period_utc from live where period_utc >= now() - interval '35 days'
)
select
  h.period_utc,
  l.yhat_mw as forecast_mw,
  l.model_version,
  l.issued_at,
  o.value as operator_mw,
  case when c.flag is null then c.demand_mw end as actual_mw,
  c.flag as actual_flag
from hours h
left join live l using (period_utc)
left join clean_demand c using (period_utc)
left join raw_eia o
  on o.period_utc = h.period_utc and o.respondent = 'CISO' and o.type = 'DF';

create view dashboard_scores as
select * from scores;

create view dashboard_backtest as
select * from backtest_results
where run_at = (select max(run_at) from backtest_results);

create view dashboard_flags_monthly as
select
  to_char((period_utc - interval '1 hour') at time zone 'America/Los_Angeles', 'YYYY-MM') as month,
  flag,
  count(*)::int as hours
from clean_demand
where flag is not null
group by 1, 2;

create view dashboard_status as
select
  (select max(period_utc) from raw_eia where type = 'D' and value is not null) as latest_demand_period,
  (select max(period_utc) from raw_eia where type = 'DF' and value is not null) as latest_operator_period,
  (select max(valid_time_utc) from raw_weather) as latest_weather_valid_time,
  (select max(issued_at) from forecasts) as latest_forecast_issued_at,
  (select max(target_time_utc) from forecasts) as latest_forecast_target,
  (select count(distinct ((target_time_utc - interval '1 hour') at time zone 'America/Los_Angeles')::date)::int
     from forecasts) as days_forecast,
  (select count(*)::int from scores where hours_scored > 0) as days_scored,
  (select max(target_date) from scores) as latest_scored_date,
  (select version from model_versions where is_active) as active_model,
  (select trained_at from model_versions where is_active) as active_model_trained_at,
  (select count(*)::int from clean_demand) as demand_hours,
  (select count(*)::int from clean_demand where flag is not null) as demand_hours_flagged;

grant select on dashboard_hourly, dashboard_scores, dashboard_backtest,
  dashboard_flags_monthly, dashboard_status to anon;
