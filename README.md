# Live-grid-demand-forecaster

A live, day-ahead electricity demand forecaster for the California ISO (EIA balancing authority `CISO`). Each morning it forecasts every hour of the next day, records the forecast immutably, and later scores it against actual demand and against CAISO's own published day-ahead forecast.

Status: M3.5 (minimal live loop). Live forecasts have been issued daily since 2026-10-12 with the seasonal-naive baseline. The full design and milestone plan are in [CLAUDE.md](CLAUDE.md); progress is logged in [docs/progress.md](docs/progress.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 itself) and Docker for the local database.

```sh
uv sync
cp .env.example .env    # then add your EIA API key
docker compose up -d    # local Postgres on localhost:54322
```

A free EIA key is available at https://www.eia.gov/opendata/register.php.

## Commands

```sh
uv run python -m gridcast.jobs.smoke              # pull 48h of CISO demand and operator forecast, print row counts
uv run python -m gridcast.jobs.ingest --backfill  # load EIA from 2019-01 and day-2 weather forecasts from 2024-01-20
uv run python -m gridcast.jobs.ingest             # re-pull the trailing 7 days (run on a schedule)
uv run python -m gridcast.jobs.clean              # screen reported demand and rebuild clean_demand
uv run python -m gridcast.jobs.backtest           # score the baselines and the operator forecast over 12 months
uv run python -m gridcast.jobs.issue --dry-run    # forecast tomorrow without recording it
uv run python -m gridcast.jobs.issue              # forecast tomorrow and record it (immutable)
uv run python -m gridcast.jobs.score              # score forecast days that ended 72+ hours ago
uv run pytest                                     # fixtures plus the local database; never the network
uv run ruff check . && uv run ruff format --check .
```

Every job applies pending migrations in `sql/migrations/` and is safe to repeat: ingest and clean upsert, issue keeps the first forecast recorded for an hour, and score never rescores a day.

Jobs use `DATABASE_URL` from `.env`, so they write to whichever database it points at. On GitHub, `ingest.yml` runs every 3 hours, `issue.yml` daily at 16:23 UTC and `score.yml` daily at 17:41 UTC.

The cleaning EDA is in [notebooks/eda_cleaning.ipynb](notebooks/eda_cleaning.ipynb). To re-run it:

```sh
uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/eda_cleaning.ipynb
```
