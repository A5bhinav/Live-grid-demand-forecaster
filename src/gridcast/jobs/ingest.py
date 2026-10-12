"""Ingest job: EIA demand and operator forecast, plus day-2 weather forecasts, into raw tables.

    uv run python -m gridcast.jobs.ingest             # re-pull the trailing revision window
    uv run python -m gridcast.jobs.ingest --backfill  # load the full history

Every run upserts, so it is safe to repeat or to resume after a failure.
"""

import argparse
import logging
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock

import httpx
import psycopg

from gridcast import config, db
from gridcast.ingest.eia import fetch_region_data
from gridcast.ingest.weather import fetch_previous_runs

log = logging.getLogger("gridcast.ingest")


@dataclass(frozen=True)
class IngestStats:
    fetched: int
    changed: int  # rows inserted or overwritten with a revised value


def date_chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Split the inclusive range into consecutive inclusive chunks of at most `days` days."""
    while start <= end:
        chunk_end = min(start + timedelta(days=days - 1), end)
        yield start, chunk_end
        start = chunk_end + timedelta(days=1)


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split the inclusive range at calendar-month boundaries."""
    while start <= end:
        next_month = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        yield start, chunk_end
        start = chunk_end + timedelta(days=1)


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, clock.min, tzinfo=UTC)


def ingest_eia(
    conn: psycopg.Connection,
    client: httpx.Client,
    api_key: str,
    start: date,
    end: date,
    *,
    min_interval_s: float = config.EIA_MIN_REQUEST_INTERVAL_S,
) -> IngestStats:
    """Upsert every published hour from `start` through `end` (UTC dates, inclusive)."""
    fetched = changed = 0
    for chunk_start, chunk_end in date_chunks(start, end, config.EIA_CHUNK_DAYS):
        rows = fetch_region_data(
            client,
            api_key,
            _utc_midnight(chunk_start),
            _utc_midnight(chunk_end + timedelta(days=1)),
            min_interval_s=min_interval_s,
        )
        chunk_changed = db.upsert_eia(conn, rows)
        conn.commit()
        log.info(
            "eia %s..%s fetched=%d changed=%d", chunk_start, chunk_end, len(rows), chunk_changed
        )
        fetched += len(rows)
        changed += chunk_changed
        time.sleep(min_interval_s)
    return IngestStats(fetched, changed)


def ingest_weather(
    conn: psycopg.Connection,
    client: httpx.Client,
    start: date,
    end: date,
    *,
    locations: Sequence[config.Location] = config.LOCATIONS,
    min_interval_s: float = config.WEATHER_MIN_REQUEST_INTERVAL_S,
) -> IngestStats:
    """Upsert day-2 forecasts valid from `start` through `end` (UTC dates, inclusive)."""
    fetched = changed = 0
    for location in locations:
        for chunk_start, chunk_end in month_chunks(start, end):
            rows = fetch_previous_runs(client, location, chunk_start, chunk_end)
            if not rows:
                log.warning(
                    "weather %s %s..%s returned no data", location.name, chunk_start, chunk_end
                )
            chunk_changed = db.upsert_weather(conn, rows)
            conn.commit()
            log.info(
                "weather %s %s..%s fetched=%d changed=%d",
                location.name,
                chunk_start,
                chunk_end,
                len(rows),
                chunk_changed,
            )
            fetched += len(rows)
            changed += chunk_changed
            time.sleep(min_interval_s)
    return IngestStats(fetched, changed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backfill", action="store_true", help="load the full history")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = config.Settings()
    api_key = settings.require_eia_api_key()
    today = datetime.now(UTC).date()
    trailing_start = today - timedelta(days=config.EIA_REVISION_WINDOW_DAYS)
    eia_start = config.EIA_BACKFILL_START if args.backfill else trailing_start
    weather_start = config.WEATHER_START if args.backfill else trailing_start
    weather_end = today + timedelta(days=config.WEATHER_HORIZON_DAYS)

    with db.connect(settings.require_database_url()) as conn, httpx.Client(timeout=60) as client:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        conn.commit()
        eia = ingest_eia(conn, client, api_key, eia_start, today)
        weather = ingest_weather(conn, client, weather_start, weather_end)

    log.info("eia total fetched=%d changed=%d", eia.fetched, eia.changed)
    log.info("weather total fetched=%d changed=%d", weather.fetched, weather.changed)


if __name__ == "__main__":
    main()
