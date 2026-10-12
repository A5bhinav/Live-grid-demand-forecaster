"""Issue job: forecast every hour of tomorrow (Pacific time) and record it.

    uv run python -m gridcast.jobs.issue [--dry-run]

A forecast is only ever issued for the day after the one it runs on, and the first
forecast recorded for an hour stands: running the job again the same day changes nothing.
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import psycopg

from gridcast import config, db, protocol
from gridcast.features.build import Sources, build_inputs
from gridcast.models.baselines import SeasonalNaive

log = logging.getLogger("gridcast.issue")


@dataclass(frozen=True)
class IssueStats:
    forecast: pd.Series
    inserted: int
    unpublished_hours: int  # hours before the demand cutoff that EIA had not published


def run(conn: psycopg.Connection, now: datetime, *, dry_run: bool = False) -> IssueStats:
    forecaster = SeasonalNaive()
    issue_date = now.astimezone(config.LOCAL_TZ).date()
    raw_demand = db.read_raw_demand(conn)
    inputs = build_inputs(issue_date, Sources(raw_demand=raw_demand))
    forecast = forecaster.predict(inputs)
    if forecast.isna().any():
        raise RuntimeError(
            f"{forecaster.version} has no value for {int(forecast.isna().sum())} target hours"
        )
    published = raw_demand.dropna().index.max()
    unpublished = max(0, int((protocol.demand_cutoff(issue_date) - published) / protocol.HOUR))
    inserted = 0 if dry_run else db.insert_forecasts(conn, forecaster.version, forecast)
    return IssueStats(forecast, inserted, unpublished)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="forecast without recording")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with db.connect(config.Settings().require_database_url()) as conn:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        stats = run(conn, datetime.now(UTC), dry_run=args.dry_run)

    forecast = stats.forecast
    if stats.unpublished_hours:
        log.warning("%d hours before the demand cutoff were unpublished", stats.unpublished_hours)
    log.info(
        "issue targets=%s..%s hours=%d inserted=%d min_mw=%.0f max_mw=%.0f%s",
        forecast.index[0].strftime("%Y-%m-%dT%H"),
        forecast.index[-1].strftime("%Y-%m-%dT%H"),
        len(forecast),
        stats.inserted,
        forecast.min(),
        forecast.max(),
        " (dry run)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
