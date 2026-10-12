# Grid Demand Forecaster: Agent Brief

> Context file for a coding agent. Read it in full before writing code.
> Put it at the repo root as `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex, Cursor) so it loads automatically.

---

## 1. What we're building

A **live, day-ahead electricity demand forecaster for the California ISO** (EIA balancing-authority code `CISO`).

Every morning the system:
1. Publishes a forecast for each hour of the next day.
2. Records that forecast immutably.
3. Later scores it against actual demand **and** against CAISO's own published day-ahead forecast.
4. Shows a running scoreboard on a public dashboard.

This is a portfolio project. It has to demonstrate the full ML lifecycle: ingesting and cleaning messy real data, leakage-safe feature engineering, honest time-based evaluation, a deployed live system, and monitoring. Engineering quality (tests, idempotency, reproducibility) matters as much as accuracy.

**Target resume line:**
> "Live day-ahead load forecaster for CAISO; X% MAPE over N days of live predictions vs. Y% for the operator's published forecast."

If we don't beat the operator, we report that honestly (e.g. "within X points of the operator's forecast"). Never tune on the live period, and never cherry-pick windows.

---

## 2. Non-negotiable rules

1. **No look-ahead leakage.** Every input to a forecast issued at `issue_time` must have existed at `issue_time`. Tests enforce this (§8); convention alone is not enough.
2. **Weather features are forecasts, never observations.** Use Open-Meteo's **Previous Runs API** with `_previous_day2` variables, in both training and live inference (§4.2).
   - Do **not** use the Historical Weather API (ERA5) for features.
   - Do **not** use the Historical Forecast API for features. It stitches together the first few hours of each model run, so it is effectively observed weather.
3. **Live forecasts are immutable.** Each forecast row is inserted once with `issued_at` and is never updated or deleted. Enforce this in the database, not just in code.
4. **Time-based evaluation only.** No random splits and no shuffled CV.
5. **Do not use the operator's forecast (`DF`) as a model feature.** It's the opponent; using it would muddy the comparison.
6. **Secrets stay in env vars and GitHub secrets.** Never print, log, or commit keys. The Supabase service-role key never goes into the web app.
7. **Ask the human before** changing the target BA, changing evaluation rules, adding any paid service, or deleting data.

---

## 3. What only the human can do

- [ ] Register a free EIA API key: https://www.eia.gov/opendata/register.php
- [ ] Create a Supabase project.
- [ ] Create the GitHub repo and add secrets: `EIA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- [ ] Create a Vercel project for `web/` with `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

If any of these is missing, don't block. Develop locally (Supabase CLI `supabase start`, or Postgres in Docker) with recorded fixtures, and list what's still needed.

---

## 4. Data sources

### 4.1 EIA-930: actual demand and the operator's forecast

| Item | Value |
|---|---|
| API | EIA API v2, free key passed as the `api_key` query param |
| Route | `https://api.eia.gov/v2/electricity/rto/region-data/data/` |
| Respondent | `facets[respondent][]=CISO` |
| Types | `D` = actual demand (MWh), `DF` = BA's day-ahead demand forecast |
| Frequency | `frequency=hourly` (UTC periods). A `local-hourly` option exists; store UTC. |
| Columns | `data[]=value` |
| Sort | `sort[0][column]=period&sort[0][direction]=asc` |
| Page size | **Max 5,000 rows per JSON response.** Paginate with `offset`/`length` and read `response.total`. |
| History | CISO goes back to mid-2015. Backfill from `2018-07-01`. |

**Verify before building:** hit the metadata endpoints `/v2/electricity/rto/region-data/` and `/v2/electricity/rto/region-data/facet/type/` to confirm the `CISO`, `D`, and `DF` codes and the hourly `start`/`end` format (expected `YYYY-MM-DDTHH`). Save the responses as test fixtures.

Example request:
```
https://api.eia.gov/v2/electricity/rto/region-data/data/?api_key=$EIA_API_KEY&frequency=hourly&data[]=value&facets[respondent][]=CISO&facets[type][]=D&facets[type][]=DF&start=2024-01-01T00&end=2024-03-01T00&sort[0][column]=period&sort[0][direction]=asc&offset=0&length=5000
```

**Quirks to handle:**
- Values come back as **strings**. Cast them to numbers and treat empty strings as missing.
- **Hour-ending convention:** a value stamped `T05` covers 04:00–05:00. Align weather to it and document the choice.
- **Data gets revised after first publication.** Every ingest run must upsert and re-pull a trailing 7-day window. Optionally keep an append-only revision history table.
- **Throttle requests.** Keys get temporarily suspended if you hammer the API.

**Known data-quality problems:** missing hours, zero or negative values, spikes, and DST artifacts.
- Ruggles et al. (2020) found 2.2% of EIA demand data missing and built a screening method for anomalies: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7250876/
- PUDL re-implements those screening heuristics (null, non-positive, rolling-median deviation, anomalous regions) and is a good reference: https://docs.catalyst.coop/pudl/en/stable/methodology/timeseries_imputation.html

**Possible v2 extension:** CISO also reports subregion demand (PG&E, SCE, SDG&E, VEA). Find the sub-BA route through the metadata endpoints.

### 4.2 Open-Meteo Previous Runs API: weather forecasts

| Item | Value |
|---|---|
| Endpoint | `https://previous-runs-api.open-meteo.com/v1/forecast` |
| Meaning | `temperature_2m_previous_dayN` = the value predicted N×24h before the valid time |
| Coverage | Most models archived from **Jan 2024**. GFS 2 m temperature goes back to **March 2021**. |
| Cost | Free for non-commercial use, no key (~10k calls/day) |
| Docs | https://open-meteo.com/en/docs/previous-runs-api |

**Why `_previous_day2`:** we issue at ~09:00 PT on day D for every hour of D+1.
- A 48h-old forecast for any D+1 hour was made no later than D-1 23:00, which is before issue time. So `_previous_day2` is always leakage-safe.
- `_previous_day1` for D+1 23:00 would come from a run at D 23:00, which is after issue time. So `_previous_day1` is not safe.
- Use `_previous_day2` in the live job too, so training and serving see the same kind of feature.
- Per-hour lead matching is a possible v2 refinement, and only if it's provably leakage-safe.

**Pin the weather model** (e.g. GFS; verify the exact `models=` id in the docs). Don't use "best match": it can pick short-horizon regional models (like HRRR) that don't populate day-2 offsets, and the blend changes over time. Before relying on any variable, confirm it is populated at `_previous_day2` for the pinned model.

**Training window:** the main model trains from `2024-01-01`, when the weather-forecast features begin. Optional ablation: a temperature-only model trained from `2021-03`, using GFS temperature only.

**v1 variables** (each with the `_previous_day2` suffix): `temperature_2m`, `dew_point_2m`, `relative_humidity_2m`, `cloud_cover`, `shortwave_radiation`, `wind_speed_10m`, `precipitation`. Request `timezone=UTC` and chunk requests by month per location.

**Locations** must be inside CAISO territory. LADWP (Los Angeles city), SMUD (Sacramento, part of BANC), and IID are separate balancing authorities, so avoid downtown LA and Sacramento.

| Point | Lat, Lon | Utility area |
|---|---|---|
| Oakland | 37.80, -122.27 | PG&E |
| San Jose | 37.34, -121.89 | PG&E |
| Fresno | 36.74, -119.79 | PG&E |
| Long Beach | 33.77, -118.19 | SCE |
| Riverside | 33.95, -117.40 | SCE |
| San Diego | 32.72, -117.16 | SDG&E |

Use equal weights in v1. In v2, weight by subregion load share.

**Weather features to derive:**
- Weighted mean, min, and max temperature
- Cooling and heating degree-hours (base 18 °C)
- Dew point
- Solar radiation and cloud cover. Behind-the-meter rooftop solar pulls midday demand down (the "duck curve"), so these matter.
- Raw per-location temperature

### 4.3 Calendar

- `holidays` package: US federal plus California holidays
- Local hour, day of week, month
- DST flags
- Day-before-holiday and day-after-holiday flags

---

## 5. Forecast protocol (the rules of the game)

**Issue time**
- Simulated issue time in backtests: **09:00 America/Los_Angeles on day D**.
- The live job runs once daily on a UTC cron that lands before 10:00 local in both PDT and PST (e.g. `23 16 * * *` = 09:23 PDT / 08:23 PST).
- Always record the actual `issued_at`.

**Target:** every local hour of D+1. That's 24 rows normally, but **23 or 25 on DST transition days**.

**Demand available as features:** actual demand only through the **end of day D-1 local** (conservative). This means hourly lags of at least 48h relative to the target hour, plus D-1 daily mean and peak. Make the cutoff a config value; relax it only once ingest logs show the real publication latency.

**Operator benchmark:** EIA `DF` values for the D+1 hours, compared as published. We don't know exactly when CAISO produced them. They may contain information newer than our 09:00 cutoff, and the README should say so.

**Scoring**
- Score each target day once actuals have settled: compute 72h after the day ends, using the latest revised values.
- **Metrics:**
  - MAPE (headline)
  - MAE and RMSE in MW
  - Daily-peak error, both magnitude and timing
  - **Win rate**: % of days our MAPE beats DF
- Break results down by month and season.

---

## 6. Modeling plan

**Baselines first.** These set the bar.
- **Seasonal naive:** same local hour, 7 days earlier.
- **Operator DF:** the opponent (scored only, never trained on).

**Main model: LightGBM**
- One direct model across all target hours, with target hour as a feature. Predict MW.
- Optional: predict demand normalized by a trailing mean to handle level drift.

**Backtest**
- Rolling-origin with an expanding window.
- Monthly origins over the last 12 months. For each origin, train on everything from `2024-01-01` up to the origin, then forecast every day of the following month by simulating the issue protocol exactly.
- Backtests must use **the same feature-builder function** as the live job.
- Tune hyperparameters only on validation folds inside each training window.

**Ablations to report:**
1. Calendar + lags only
2. Plus temperature
3. Plus solar and cloud

**Retraining:** weekly, with a promotion gate. A new model only goes live if its recent-weeks backtest is no worse than the active model's by more than a set tolerance.

**Stretch goals (after the GBM is live):**
- P10/P90 quantile forecasts with coverage metrics
- Subregion models
- A small neural baseline for comparison

---

## 7. Architecture

```
GitHub Actions (cron, UTC)
  ingest.yml   every 3h  : EIA D/DF (trailing-7-day upsert) + weather previous runs → raw tables
  issue.yml    daily     : build features at issue_time → predict D+1 → INSERT into forecasts
  score.yml    daily     : join forecasts + actuals + DF → scores; run checks → alert
  retrain.yml  weekly    : retrain → backtest gate → register model version
  ci.yml       on PR     : ruff, pytest (no network; fixtures only)

Supabase Postgres  : raw_eia, raw_weather, clean_demand, forecasts, model_versions, scores (+ read-only views)
Supabase Storage   : models/ bucket, artifacts versioned by date + git sha
web/ (Next.js + TypeScript + Recharts on Vercel) : reads read-only views via anon key + RLS
```

**Schema sketch:**
```sql
create table raw_eia (
  respondent text, type text, period_utc timestamptz, value numeric,
  ingested_at timestamptz default now(),
  primary key (respondent, type, period_utc)
);

create table raw_weather (
  location text, valid_time_utc timestamptz, lead_days int, model text,
  variable text, value numeric, ingested_at timestamptz default now(),
  primary key (location, valid_time_utc, lead_days, model, variable)
);

create table clean_demand (
  period_utc timestamptz primary key, demand_mw numeric,
  flag text, imputed boolean default false
);

create table forecasts (
  id bigserial primary key, issued_at timestamptz not null,
  target_time_utc timestamptz not null, model_version text not null,
  yhat_mw numeric not null,
  unique (model_version, issued_at, target_time_utc)
);
-- add a trigger that raises on UPDATE or DELETE of forecasts

create table model_versions (
  version text primary key, git_sha text, trained_at timestamptz,
  train_start timestamptz, train_end timestamptz,
  params jsonb, backtest jsonb, is_active boolean default false
);
```

**Imputation rule:** impute only for building training features. Never score against imputed actuals; skip flagged hours in scoring and report how many were skipped.

**Dashboard pages:**
1. **Tomorrow / yesterday:** our forecast vs. DF vs. actual, hourly.
2. **Scoreboard:** rolling 7- and 30-day MAPE, win rate, days live.
3. **Backtest:** results and ablations.
4. **Data quality:** flagged hours over time, ingest freshness.

---

## 8. Tests that matter

- **Leakage test (most important):** for random issue times, assert that the latest timestamp of every feature input is at or before the cutoff, and that weather inputs only use `lead_days = 2`.
- **Feature parity:** building features for a past issue date from the database produces the same rows the backtest used.
- **Ingest idempotency:** running ingest twice yields the same row counts, and revised values overwrite old ones.
- **Pagination:** mocked EIA responses with `total` > 5,000.
- **DST:** target days with 23 and 25 hours.
- **Cleaning:** synthetic gaps, zeros, negatives, and spikes get the right flags.
- **Immutability:** an UPDATE or DELETE on `forecasts` fails.

CI uses recorded fixtures only, never the live network.

---

## 9. Milestones

**Priority: get the live loop running early.** The live track record only accumulates once forecasts are being issued daily. Ship a minimal live loop (seasonal naive, or the first GBM) right after M3, then upgrade the model. `model_version` on every forecast row keeps the scoreboard honest across upgrades.

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold | uv project, ruff, pytest, CI, `.env.example`, config module, and a smoke script that pulls 48h of CISO D/DF and prints row counts |
| M1 | Ingest + backfill | EIA from 2018-07 and weather (day-2) from 2024-01 for all 6 points loaded; idempotent; tests pass |
| M2 | Cleaning | Screening flags implemented and tested; EDA notebook shows % flagged by year and examples |
| M3 | Baselines + backtest harness | 12-month backtest table for seasonal naive and DF; this is the bar |
| M3.5 | Minimal live loop | Daily issue and score jobs running with the best available model; immutability enforced |
| M4 | LightGBM + ablations | Backtest beats seasonal naive clearly; honest comparison to DF; model registered and promoted |
| M5 | Dashboard | Live on Vercel, reading read-only views |
| M6 | Monitoring + README | Alerts working; README has an architecture diagram, results table, leakage rules, and limitations |

After each milestone, append a short entry to `docs/progress.md` covering what changed, key numbers, and open issues.

---

## 10. Monitoring and alerts

- **Job failure:** GitHub's failure email, plus an optional Discord or Slack webhook.
- **Freshness:** no forecast for tomorrow by 11:00 PT → alert.
- **Data quality:**
  - Missing hours in the last 24h
  - Flagged share above threshold
  - Weather fetch returned empty or partial data
- **Performance:** rolling 7-day MAPE above 1.5× the backtest MAPE → alert.

---

## 11. Gotchas

- **GitHub cron**
  - Scheduled runs can be delayed under load, so avoid minute `:00`.
  - In public repos, schedules auto-disable after 60 days with no repo activity.
- **EIA values** are strings, and empty strings occur.
- **DST:** never assume 24 rows per day.
- **Time conventions:** EIA uses hour-ending timestamps; check what Open-Meteo's timestamps mean per variable (instant vs. preceding-hour average).
- **Territory:** LADWP, SMUD/BANC, and IID are not CAISO. Keep weather points in CAISO territory.
- **Weather APIs:** the Historical Forecast API is not a day-ahead forecast. Use the Previous Runs API.
- **License:** Open-Meteo's free tier is for non-commercial use, which is fine for a portfolio.

---

## 12. Stack

- **Python:** 3.12, uv, pandas or polars, lightgbm, scikit-learn, httpx, tenacity (retries), pandera (data checks), psycopg, pytest, ruff, `holidays`
- **Web:** Next.js, TypeScript, Recharts, `@supabase/supabase-js`
- **Infra:** Supabase Postgres + Storage, GitHub Actions, Vercel

Suggested layout:
```
grid-forecaster/
  CLAUDE.md / AGENTS.md        (this file)
  pyproject.toml
  src/gridcast/
    config.py   db.py
    ingest/      eia.py  weather.py
    clean/       screen.py
    features/    build.py          # single function used by backtest AND live
    models/      baselines.py  gbm.py  registry.py
    backtest/    rolling.py
    jobs/        ingest.py  issue.py  score.py  retrain.py
  sql/migrations/
  tests/         fixtures/
  notebooks/     (EDA only, nothing the pipeline depends on)
  docs/          progress.md  architecture.png
  web/           (Next.js app)
  .github/workflows/
```

---

## 13. First session checklist

1. Read this file. List any missing secrets or accounts; continue locally if they're absent.
2. Build M0.
3. Call the EIA metadata endpoints, confirm `CISO` / `D` / `DF` and the period format, and save fixtures.
4. Call the Open-Meteo Previous Runs API for one location and one week with the pinned model. Confirm the `_previous_day2` columns are populated, and save a fixture.
5. Report back:
   - What was confirmed
   - Anything that differs from this brief
   - The plan for M1

---

## 14. References

- EIA-930 overview: https://www.eia.gov/electricity/930-content/about.html
- EIA API v2 docs: https://www.eia.gov/opendata/documentation.php
- Open-Meteo Previous Runs API: https://open-meteo.com/en/docs/previous-runs-api
- Open-Meteo Historical Forecast API (read to understand why we don't use it for features): https://open-meteo.com/en/docs/historical-forecast-api
- Ruggles et al. 2020, screening and imputing EIA demand data: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7250876/
- PUDL time-series imputation notes: https://docs.catalyst.coop/pudl/en/stable/methodology/timeseries_imputation.html