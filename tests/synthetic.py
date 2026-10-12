"""Synthetic demand and weather for tests: demand rises with temperature above 18 C."""

from datetime import date

import numpy as np
import pandas as pd
import psycopg

from gridcast import config, db
from gridcast.features.build import Sources
from gridcast.ingest.eia import EiaRow
from gridcast.ingest.weather import WeatherRow

VARIABLE_LEVELS = {
    "dew_point_2m": 9.0,
    "relative_humidity_2m": 55.0,
    "cloud_cover": 30.0,
    "wind_speed_10m": 8.0,
    "precipitation": 0.0,
}


def synthetic_sources(start: date | str, days: int) -> Sources:
    """Hourly demand and day-2 weather rows for `days` days from `start` (UTC)."""
    index = pd.date_range(str(start), periods=days * 24, freq="h", tz="UTC", name="period_utc")
    local = index.tz_convert(config.LOCAL_TZ)
    rng = np.random.default_rng(0)

    hours = np.arange(len(index))
    # A multi-day swing with a period that is not a whole number of weeks, so copying
    # last week is a poor forecast and temperature carries real information.
    regional = (
        19
        + 7 * np.sin(hours / (24 * 9.3) * 2 * np.pi)
        + 6 * np.sin((local.hour - 9) / 24 * 2 * np.pi)
    )
    daily_cycle = 4000 * np.sin((local.hour - 9) / 24 * 2 * np.pi)
    weekend = np.where(local.dayofweek >= 5, -1500, 0)
    cooling = 450 * np.clip(regional - config.DEGREE_HOUR_BASE_C, 0, None)
    demand = 23000 + daily_cycle + weekend + cooling + rng.normal(0, 120, len(index))

    rows = []
    for offset, location in enumerate(config.LOCATIONS):
        frame = pd.DataFrame(
            {
                "location": location.name,
                "valid_time_utc": index,
                "lead_days": config.WEATHER_LEAD_DAYS,
                "model": config.WEATHER_MODEL,
                "temperature_2m": regional + offset - 2.5 + rng.normal(0, 0.3, len(index)),
                "shortwave_radiation": np.clip(
                    600 * np.sin((local.hour - 6) / 24 * 2 * np.pi), 0, None
                ),
                **VARIABLE_LEVELS,
            }
        )
        rows.append(frame)
    weather = pd.concat(rows, ignore_index=True)
    return Sources(raw_demand=pd.Series(demand, index=index, name="demand_mw"), weather=weather)


def load_into_database(conn: psycopg.Connection, sources: Sources) -> None:
    """Write synthetic sources to the raw tables, as the ingest job would."""
    db.upsert_eia(
        conn,
        [
            EiaRow("CISO", "D", period.to_pydatetime(), float(value))
            for period, value in sources.raw_demand.items()
        ],
    )
    variables = list(config.WEATHER_VARIABLES)
    db.upsert_weather(
        conn,
        [
            WeatherRow(
                row.location,
                row.valid_time_utc.to_pydatetime(),
                row.lead_days,
                row.model,
                {variable: float(getattr(row, variable)) for variable in variables},
            )
            for row in sources.weather.itertuples()
        ],
    )
    conn.commit()


def insert_past_forecast(conn: psycopg.Connection, version: str, forecast: pd.Series) -> None:
    """Seed a forecast for hours that have already happened, which the table forbids."""
    conn.execute("alter table forecasts disable trigger forecasts_stamp_issue")
    with conn.cursor() as cur:
        cur.executemany(
            "insert into forecasts (issued_at, target_time_utc, model_version, yhat_mw) "
            "values (%s, %s, %s, %s)",
            [
                (period - pd.Timedelta(days=2), period.to_pydatetime(), version, float(value))
                for period, value in forecast.items()
            ],
        )
    conn.execute("alter table forecasts enable trigger forecasts_stamp_issue")
    conn.commit()
