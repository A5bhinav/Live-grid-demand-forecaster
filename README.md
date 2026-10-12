# Live-grid-demand-forecaster

A live, day-ahead electricity demand forecaster for the California ISO (EIA balancing authority `CISO`). Each morning it forecasts every hour of the next day, records the forecast immutably, and later scores it against actual demand and against CAISO's own published day-ahead forecast.

Status: M1 (ingest and backfill). The full design and milestone plan are in [CLAUDE.md](CLAUDE.md); progress is logged in [docs/progress.md](docs/progress.md).

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
uv run pytest                                     # fixtures plus the local database; never the network
uv run ruff check . && uv run ruff format --check .
```

Ingest runs apply any pending migrations in `sql/migrations/` and upsert, so they are safe to repeat.
