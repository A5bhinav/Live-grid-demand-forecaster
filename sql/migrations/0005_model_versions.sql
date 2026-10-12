-- Trained models. The artifact itself lives in object storage at `artifact_path`.
-- At most one version is active; the issue job forecasts with that one.

create table model_versions (
  version       text        primary key,
  git_sha       text,
  trained_at    timestamptz not null default now(),
  train_start   timestamptz,
  train_end     timestamptz,
  params        jsonb       not null,
  backtest      jsonb,  -- the promotion gate's result for this version
  artifact_path text,
  is_active     boolean     not null default false
);

create unique index model_versions_one_active on model_versions (is_active) where is_active;

alter table model_versions enable row level security;
