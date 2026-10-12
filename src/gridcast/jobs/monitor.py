"""Monitor job: check that the pipeline is healthy and alert when it is not.

    uv run python -m gridcast.jobs.monitor

It exits non-zero when any check fails, so GitHub sends its failure email, and also posts
to ALERT_WEBHOOK_URL when that is set.
"""

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import psycopg

from gridcast import alerts, config, db, protocol

log = logging.getLogger("gridcast.monitor")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def forecast_issued(conn: psycopg.Connection, now: datetime) -> Check:
    """Tomorrow's forecast must be recorded by 11:00 Pacific."""
    local = now.astimezone(config.LOCAL_TZ)
    if local.hour < config.FORECAST_DUE_HOUR_LOCAL:
        return Check("forecast issued", True, "not due yet today")
    targets = protocol.target_periods(local.date())
    recorded = db.read_live_forecasts(conn, targets[0]).reindex(targets).notna().sum()
    detail = f"{recorded} of {len(targets)} hours recorded for {targets[0]:%Y-%m-%d} UTC onward"
    return Check("forecast issued", recorded == len(targets), detail)


def demand_complete(conn: psycopg.Connection, now: datetime) -> Check:
    """No reported-demand hours missing over the last day, allowing for publication lag."""
    latest_due = pd.Timestamp(now).floor("h") - pd.Timedelta(
        hours=config.DEMAND_PUBLICATION_LAG_HOURS
    )
    due = pd.date_range(end=latest_due, periods=24, freq="h")
    present = db.read_raw_demand(conn).reindex(due).notna().sum()
    return Check(
        "demand complete", present == 24, f"{24 - present} of the last 24 due hours missing"
    )


def flagged_share(conn: psycopg.Connection) -> Check:
    """The share of the last week's demand that failed screening stays small."""
    flagged, hours = conn.execute(
        "select count(*) filter (where flag is not null), count(*) from clean_demand "
        "where period_utc > (select max(period_utc) from clean_demand) - interval '7 days'"
    ).fetchone()
    share = flagged / hours if hours else 1.0
    detail = f"{flagged} of {hours} hours flagged in the last 7 days"
    return Check("flagged share", share <= config.MAX_FLAGGED_SHARE, detail)


def weather_complete(conn: psycopg.Connection, now: datetime) -> Check:
    """Every location has every variable for each of tomorrow's hours."""
    targets = protocol.target_periods(now.astimezone(config.LOCAL_TZ).date())
    weather = db.read_raw_weather(conn, since=targets[0])
    weather = weather[weather["valid_time_utc"] <= targets[-1]]
    complete = weather.dropna(subset=list(config.WEATHER_VARIABLES))
    expected = len(targets) * len(config.LOCATIONS)
    detail = f"{len(complete)} of {expected} location-hours complete for tomorrow"
    return Check("weather complete", len(complete) == expected, detail)


def performance(conn: psycopg.Connection) -> Check:
    """The rolling 7-day live MAPE stays within 1.5x of the backtest MAPE."""
    recent = conn.execute(
        "select mape, hours_scored from scores where hours_scored > 0 "
        "order by target_date desc limit 7"
    ).fetchall()
    backtest = conn.execute(
        "select mape from dashboard_backtest where breakdown = 'overall' and model = 'gbm_full'"
    ).fetchone()
    if len(recent) < config.MIN_SCORED_DAYS_FOR_ALERT or backtest is None:
        return Check("performance", True, f"{len(recent)} scored days; too few to judge")
    hours = sum(scored for _, scored in recent)
    live = sum(float(mape) * scored for mape, scored in recent) / hours
    limit = float(backtest[0]) * config.PERFORMANCE_ALERT_RATIO
    detail = f"7-day MAPE {live:.2f}% against a limit of {limit:.2f}%"
    return Check("performance", live <= limit, detail)


def outside_scheduler(conn: psycopg.Connection) -> Check:
    """Where the outside scheduler is installed, GitHub accepted its recent calls."""
    if conn.execute("select to_regclass('net._http_response')").fetchone()[0] is None:
        return Check("outside scheduler", True, "not installed")
    refused, calls = conn.execute(
        "select count(*) filter "
        "(where error_msg is not null or status_code not between 200 and 299), count(*) "
        "from net._http_response"
    ).fetchone()
    return Check("outside scheduler", refused == 0, f"{refused} of {calls} recent calls refused")


def run(conn: psycopg.Connection, now: datetime) -> list[Check]:
    return [
        forecast_issued(conn, now),
        demand_complete(conn, now),
        flagged_share(conn),
        weather_complete(conn, now),
        performance(conn),
        outside_scheduler(conn),
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = config.Settings()
    with db.connect(settings.require_database_url()) as conn:
        db.migrate(conn)
        checks = run(conn, datetime.now(UTC))
    for check in checks:
        log.log(logging.INFO if check.ok else logging.ERROR, "%s: %s", check.name, check.detail)
    failed = [check for check in checks if not check.ok]
    if failed:
        text = "Grid forecaster alert: " + "; ".join(f"{c.name} ({c.detail})" for c in failed)
        url = settings.alert_webhook_url
        alerts.send_alert(text, url.get_secret_value() if url else None)
        sys.exit(1)


if __name__ == "__main__":
    main()
