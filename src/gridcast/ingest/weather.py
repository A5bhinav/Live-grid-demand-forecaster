"""Open-Meteo Previous Runs API: archived weather forecasts, never observations.

`<variable>_previous_dayN` is the value forecast N x 24h before the valid time. Timestamps
are stored as Open-Meteo returns them: instantaneous variables are valid at the timestamp,
while `shortwave_radiation` and `precipitation` cover the preceding hour.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from gridcast import config
from gridcast.ingest.http import get_json


@dataclass(frozen=True)
class WeatherRow:
    location: str
    valid_time_utc: datetime
    lead_days: int
    model: str
    values: dict[str, float | None]  # by variable name


def fetch_previous_runs(
    client: httpx.Client,
    location: config.Location,
    start_date: date,
    end_date: date,
    *,
    model: str = config.WEATHER_MODEL,
    lead_days: int = config.WEATHER_LEAD_DAYS,
    variables: Sequence[str] = config.WEATHER_VARIABLES,
) -> list[WeatherRow]:
    """Fetch hourly forecasts valid from `start_date` through `end_date` (UTC, inclusive).

    Hours the archive has no value for are omitted, so a later run can fill them in.
    """
    if lead_days < config.WEATHER_LEAD_DAYS:
        raise ValueError(
            f"lead_days={lead_days} can postdate the issue time; "
            f"the minimum leakage-safe offset is {config.WEATHER_LEAD_DAYS}"
        )
    suffix = f"_previous_day{lead_days}"
    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "hourly": ",".join(variable + suffix for variable in variables),
        "models": model,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "timezone": "UTC",
    }
    hourly = get_json(client, config.WEATHER_URL, params)["hourly"]
    valid_times = [datetime.fromisoformat(stamp).replace(tzinfo=UTC) for stamp in hourly["time"]]
    rows = []
    for position, valid_time in enumerate(valid_times):
        values = {variable: hourly[variable + suffix][position] for variable in variables}
        if any(value is not None for value in values.values()):
            rows.append(WeatherRow(location.name, valid_time, lead_days, model, values))
    return rows
