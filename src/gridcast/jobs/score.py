"""Score job: score each forecast day once its actuals have settled.

    uv run python -m gridcast.jobs.score

A day is scored 72 hours after it ends, against the latest revised demand, and is never
scored again.
"""

import logging
from datetime import UTC, datetime

import pandas as pd
import psycopg

from gridcast import config, db, protocol, scoring
from gridcast.clean.screen import clean

log = logging.getLogger("gridcast.score")
FORECAST = "forecast"


def run(conn: psycopg.Connection, now: datetime) -> list[dict[str, object]]:
    pending = db.read_unscored_forecasts(conn)
    if pending.empty:
        return []
    settled = pd.Timestamp(now) - pd.Timedelta(hours=config.SCORING_DELAY_HOURS)
    cleaned = clean(db.read_raw_demand(conn))
    operator = db.read_raw_eia(conn, config.EIA_TYPE_OPERATOR_FORECAST)

    pending["target_date"] = protocol.local_hour_start(pd.DatetimeIndex(pending["period_utc"])).date
    rows = []
    for (model_version, target_date), day in pending.groupby(["model_version", "target_date"]):
        if day["period_utc"].max() > settled:
            continue
        forecasts = pd.DataFrame(
            {FORECAST: day["yhat_mw"].to_numpy()}, index=pd.DatetimeIndex(day["period_utc"])
        )
        forecasts[scoring.OPERATOR] = operator.reindex(forecasts.index)
        frame = scoring.score_frame(forecasts, cleaned)
        rows.append(
            {
                "model_version": model_version,
                "target_date": target_date,
                **scoring.day_score(frame, FORECAST),
            }
        )
    db.insert_scores(conn, rows)
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with db.connect(config.Settings().require_database_url()) as conn:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        rows = run(conn, datetime.now(UTC))
    for row in rows:
        log.info(
            "scored %s %s hours=%d/%d mape=%s operator_mape=%s",
            row["model_version"],
            row["target_date"],
            row["hours_scored"],
            row["hours"],
            row.get("mape"),
            row.get("operator_mape"),
        )
    log.info("score days=%d", len(rows))


if __name__ == "__main__":
    main()
