# Progress log

## M0: Scaffold (2026-10-12)

**What changed**
- The agent brief moved from `README.md` to `CLAUDE.md`; `README.md` is now a short setup guide.
- uv project `gridcast` pinned to Python 3.12, with ruff, pytest and a CI workflow (`.github/workflows/ci.yml`).
- `gridcast.config`: secrets via pydantic-settings (`SecretStr`, so keys are masked in reprs), plus the protocol constants from the brief.
- `gridcast.ingest.eia.fetch_region_data`: paginated EIA-930 client with retries on 429/5xx and transport errors.
- `gridcast.jobs.smoke`: pulls 48h of CISO `D`/`DF` and prints row counts.
- 15 tests, all on fixtures. `tests/conftest.py` blocks socket connections so a test cannot reach the network.

**Key numbers**
- Smoke run at 2026-10-12 08:39 UTC with the public `DEMO_KEY`: 47 of 48 hours for both `D` and `DF`, no empty values. The newest hour was not yet published, so publication lag was about 1-2 hours.

**Confirmed against the live APIs**
- EIA: `CISO`, `D` and `DF` codes; period format `YYYY-MM-DDTHH`; `value` and `total` both arrive as strings.
- Open-Meteo Previous Runs: `models=gfs_seamless` returns all seven v1 variables fully populated at `_previous_day2` (checked for Oakland, 2026-10-08).

**Differences from the brief**
- The EIA key is sent as an `X-Api-Key` header rather than the `api_key` query param. EIA accepts both, and the header keeps the key out of URLs that httpx logs and puts in exception messages.
- EIA publishes `DF` only up to the latest hour that has actuals, not for future hours. Scoring is unaffected, but the dashboard's "tomorrow" view cannot show the operator forecast from EIA.

**Open issues**
- No personal `EIA_API_KEY` yet; `DEMO_KEY` is rate-limited and not suitable for the M1 backfill.
- Supabase project, GitHub secrets and Vercel project are not set up. M1 can develop against `supabase start` locally.
- EIA metadata endpoint responses and a weather fixture are not yet saved (first-session steps 3 and 4).
- CI has not run on GitHub yet; nothing is committed or pushed.

## M1: Ingest and backfill (2026-10-12)

**What changed**
- `sql/migrations/0001_raw_tables.sql`: `raw_eia` and `raw_weather`, both with row-level security on. `gridcast.db.migrate` applies migrations in name order and records them in `schema_migrations`.
- `gridcast.db.upsert_eia` / `upsert_weather`: COPY into a staging table, then `insert ... on conflict do update` only where the value differs. They return the number of rows inserted or revised.
- `gridcast.ingest.weather.fetch_previous_runs`: Open-Meteo Previous Runs client. It refuses `lead_days` below 2.
- `gridcast.jobs.ingest`: trailing 7-day re-pull by default, `--backfill` for full history. EIA is chunked by 90 days, weather by calendar month per location, with a commit per chunk so a failed run can resume.
- Local Postgres 17 via `docker-compose.yml`; CI gets a Postgres service container.
- Fixtures recorded: EIA route metadata, EIA type facet, and one week of Oakland day-2 weather.
- 37 tests. Database tests run in a throwaway schema per test.

**Key numbers** (local database, backfill run 2026-10-12 08:51-08:55 UTC, about 4 minutes)
- `raw_eia`: 136,263 rows. `D` 68,191 rows from 2019-01-01T00 to 2026-10-12T08; `DF` 68,072 rows to 2026-10-12T07.
- `D` gaps: 2 hours absent and 22 published empty, all in 2019-2023. No zero or negative values, but the minimum is 14 MWh against a median of 24,408, so there are low outliers for M2 to flag.
- `DF` gaps: 120 hours absent, all in 2024-2026 (49, 48 and 23 by year).
- `raw_weather`: 1,006,488 rows = 6 locations x 23,964 hours x 7 variables, from 2024-01-20T12 to 2026-10-14T23 with no gaps.
- Sizes: `raw_eia` 13 MB, `raw_weather` 179 MB.
- Idempotency on real data: two trailing runs straight after the backfill fetched 353 EIA and 10,080 weather rows each and changed 0.

**Differences from the brief**
- EIA history starts 2019-01-01, not mid-2015. The route metadata reports `startPeriod` 2019-01-01T00 and queries for 2015-2018 return 0 rows, so `EIA_BACKFILL_START` is 2019-01-01 rather than 2018-07-01.
- The day-2 weather archive starts 2024-01-20T12Z for all seven variables, not 2024-01-01. Only `temperature_2m` goes back further (2021-03-25). The main model's training window therefore starts about three weeks later than planned.
- The weather model is pinned to `gfs_global`, not `gfs_seamless` as noted in M0. The two returned identical day-2 values in two spot checks (Riverside, 2024-07 and 2026-09/10), and `gfs_global` is a single model rather than a blend.
- Raw tables have an `updated_at` column next to `ingested_at`, so first-seen time (publication latency) and last-revision time are both kept.
- A `DATABASE_URL` secret is needed in addition to the three in the brief, because the pipeline talks to Postgres directly through psycopg.

**Open issues**
- The data is in the local Docker database only. Once the Supabase project exists, set `DATABASE_URL` and re-run the backfill there.
- `raw_weather` at 179 MB is about a third of Supabase's 500 MB free tier and grows roughly 65 MB a year. A wide table (one row per location and hour) would be about a tenth of the size.
- `.github/workflows/ingest.yml` (every 3 hours) is not written yet; it needs the hosted database and GitHub secrets first.
- `_previous_day2` is populated for hours more than two days ahead (seen out to +4 days), so values for far-future hours are presumably provisional and settle once the valid time is within 48 hours. Before M3.5, confirm that the values the live job reads for D+1 match what the archive later holds, and consider snapshotting the features used at issue time.
- No revision-history table; only the latest value and its `updated_at` are kept.
- CI has still not run on GitHub; nothing is committed or pushed.

## M2: Cleaning (2026-10-12)

**What changed**
- `gridcast.clean.screen`: `screen` flags suspect hours, `impute` fills them, `clean` does both. All three are pure functions of a demand series.
- Flags, in the order they are applied: `missing`, `non_positive`, `global_outlier` (10x from the overall median), `repeated_value` (same value three or more times, each within a day of the last), `spike` (a jump over 4 rolling IQRs of the hourly change, reversed within 6 hours), `local_outlier_high` / `_low` (1.5x from a rolling-median estimate), `anomalous_region` (over 15% of the surrounding 24 hours flagged).
- Imputation: gaps of up to 3 hours are interpolated; longer or trailing gaps copy the same hours a week earlier, shifted to meet the good values at the gap's edges. `anomalous_region` hours keep their reported value.
- `sql/migrations/0002_clean_demand.sql` and `gridcast.jobs.clean`, which re-screens the full history and upserts `clean_demand`.
- `notebooks/eda_cleaning.ipynb`, committed with outputs. Notebook dependencies are in a separate `notebooks` group and notebooks are excluded from lint.
- 61 tests (24 new), covering synthetic gaps, zeros, negatives, spikes, stuck values, sustained level errors, a heat wave and both daylight-saving transitions that must not be flagged, and the job's idempotency.

**Key numbers**
- 122 of 68,193 hours flagged (0.18%): 54 `anomalous_region`, 24 `missing`, 24 `spike`, 17 `repeated_value`, 3 `global_outlier`. 101 of them are in 2019; no year since has more than 6.
- 68 hours imputed, none left unfilled.
- Imputation check on hidden good hours: 1.09% mean absolute error for 500 single hours (95th percentile 2.83%), 2.79% for fifty 8-hour blocks (95th percentile 8.38%).
- The clean job takes under a second and a second run changes 0 rows.

**Differences from the brief**
- The local outlier screen does not use the IQR thresholds from Ruggles et al. A first version with them flagged about 5% of hours (888 "low" outliers), and reported demand matched the operator forecast on most of those, so they were real weekend middays. The screen now uses a plain 1.5x ratio and short glitches are left to the `spike` screen.
- `clean_demand` has an `updated_at` column in addition to the sketch.

**Findings that affect later milestones**
- Reported demand (`D`) and the operator forecast (`DF`) have diverged at midday since 2023. The average gap at 12:00 local grew from 0.6 GW in 2021 to 2.4 (2023), 4.2 (2024), 5.9 (2025) and 7.8 GW (2026). The operator forecast's MAPE against `D` is 9.1% in 2026: 21.6% between 09:00 and 16:00 local and 3.0% otherwise. The two series probably no longer measure the same quantity; battery charging being counted in `D` would fit the shape but is unverified. Scoring `DF` against `D` as-is would understate the operator badly. This needs a decision before M3.
- `DF` is absent for whole days around daylight-saving transitions since 2024 (49, 23, 25 and 23 hours), so those days have no operator benchmark.
- Screening uses centered windows, so a flag can depend on later hours. `clean_demand` is therefore a hindsight table for scoring and the dashboard. The feature builder must call `clean` on demand truncated at the issue-time cutoff instead of reading the table; the leakage test in M3/M4 should cover this.

**Open issues**
- `DF` is not screened. It has no near-zero or stuck values, but nothing checks it.
- The clean job is not yet part of a scheduled workflow.
