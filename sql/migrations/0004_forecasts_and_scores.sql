-- The live record. A forecast row is written once and can never be changed, removed or
-- backdated: triggers stamp the issue time from the database clock, reject any forecast
-- for an hour that has already started, and reject UPDATE, DELETE and TRUNCATE.

create table forecasts (
  id              bigserial   primary key,
  issued_at       timestamptz not null default now(),
  target_time_utc timestamptz not null,  -- hour-ending
  model_version   text        not null,
  yhat_mw         numeric     not null,
  unique (model_version, target_time_utc)  -- the first forecast for an hour is the record
);

create index forecasts_target_time_idx on forecasts (target_time_utc);

create function forecasts_stamp_issue() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.issued_at := now();
  if new.target_time_utc - interval '1 hour' <= new.issued_at then
    raise exception 'forecast for % issued after that hour started', new.target_time_utc;
  end if;
  return new;
end $$;

create function forecasts_reject_change() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'forecasts are immutable: % is not allowed', tg_op;
end $$;

create trigger forecasts_stamp_issue before insert on forecasts
  for each row execute function forecasts_stamp_issue();
create trigger forecasts_no_update_or_delete before update or delete on forecasts
  for each row execute function forecasts_reject_change();
create trigger forecasts_no_truncate before truncate on forecasts
  for each statement execute function forecasts_reject_change();

-- One row per model and local target day, written once the day's actuals have settled.
-- The model and the operator are scored on the same hours.
create table scores (
  model_version             text    not null,
  target_date               date    not null,  -- local (Pacific) day
  hours                     int     not null,  -- 23, 24 or 25
  hours_scored              int     not null,
  mape                      numeric,
  mae_mw                    numeric,
  rmse_mw                   numeric,
  peak_error_mw             numeric,  -- forecast peak minus actual peak; complete days only
  peak_hours_off            numeric,
  operator_mape             numeric,
  operator_mae_mw           numeric,
  operator_rmse_mw          numeric,
  operator_peak_error_mw    numeric,
  operator_peak_hours_off   numeric,
  other_hours_scored        int     not null,  -- outside 09:00-17:00 local
  other_hours_mape          numeric,
  operator_other_hours_mape numeric,
  scored_at                 timestamptz not null default now(),
  primary key (model_version, target_date)
);

alter table forecasts enable row level security;
alter table scores enable row level security;
