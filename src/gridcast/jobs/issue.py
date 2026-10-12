"""Issue job: forecast every hour of tomorrow (Pacific time) and record it.

    uv run python -m gridcast.jobs.issue [--dry-run]

It forecasts with the active model version, or the seasonal-naive baseline if none is
active. A forecast is only ever issued for the day after the one it runs on, and the first
forecast recorded for a day stands: running the job again changes nothing, even after a
different model has been promoted.
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import psycopg

from gridcast import config, db, protocol
from gridcast.backtest.rolling import Forecaster
from gridcast.features.build import Sources, build_inputs
from gridcast.models import registry

log = logging.getLogger("gridcast.issue")


@dataclass(frozen=True)
class IssueStats:
    forecast: pd.Series
    inserted: int
    already_issued: bool  # the target day had a live forecast before this run
    unpublished_hours: int  # hours before the demand cutoff that EIA had not published
    hours_without_weather: int  # target hours with no archived weather forecast


def run(
    conn: psycopg.Connection, forecaster: Forecaster, now: datetime, *, dry_run: bool = False
) -> IssueStats:
    issue_date = now.astimezone(config.LOCAL_TZ).date()
    cutoff = protocol.demand_cutoff(issue_date)
    raw_demand = db.read_raw_demand(conn)
    weather = db.read_raw_weather(
        conn, since=cutoff - pd.Timedelta(days=config.FEATURE_LOOKBACK_DAYS)
    )
    inputs = build_inputs(issue_date, Sources(raw_demand=raw_demand, weather=weather))
    forecast = forecaster.predict(inputs)
    if forecast.isna().any():
        raise RuntimeError(
            f"{forecaster.version} has no value for {int(forecast.isna().sum())} target hours"
        )

    already_issued = not db.read_live_forecasts(conn, inputs.targets[0]).empty
    inserted = 0
    if not (dry_run or already_issued):
        inserted = db.insert_forecasts(conn, forecaster.version, forecast)
    published = raw_demand.dropna().index.max()
    temperature = inputs.weather["temperature_2m"].reindex(inputs.targets)
    return IssueStats(
        forecast=forecast,
        inserted=inserted,
        already_issued=already_issued,
        unpublished_hours=max(0, int((cutoff - published) / protocol.HOUR)),
        hours_without_weather=int(temperature.isna().all(axis=1).sum()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="forecast without recording")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = config.Settings()
    with db.connect(settings.require_database_url()) as conn:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        forecaster = registry.active_forecaster(conn, registry.artifact_store(settings))
        stats = run(conn, forecaster, datetime.now(UTC), dry_run=args.dry_run)

    forecast = stats.forecast
    if stats.unpublished_hours:
        log.warning("%d hours before the demand cutoff were unpublished", stats.unpublished_hours)
    if stats.hours_without_weather:
        log.warning("%d target hours have no weather forecast", stats.hours_without_weather)
    if stats.already_issued:
        log.info("a forecast for this day is already recorded; nothing inserted")
    log.info(
        "issue model=%s targets=%s..%s hours=%d inserted=%d min_mw=%.0f max_mw=%.0f%s",
        forecaster.version,
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
