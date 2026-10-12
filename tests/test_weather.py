from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from gridcast import config
from gridcast.ingest.weather import fetch_previous_runs

OAKLAND = config.LOCATIONS[0]
START = date(2026, 10, 1)
END = date(2026, 10, 7)


def make_client(
    payload: dict[str, Any], requests: list[httpx.Request] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_recorded_response(open_meteo_previous_runs: dict[str, Any]) -> None:
    rows = fetch_previous_runs(make_client(open_meteo_previous_runs), OAKLAND, START, END)

    hours = 7 * 24
    assert len(rows) == hours * len(config.WEATHER_VARIABLES)
    assert {row.variable for row in rows} == set(config.WEATHER_VARIABLES)
    assert {row.location for row in rows} == {"oakland"}
    assert {row.model for row in rows} == {config.WEATHER_MODEL}
    assert min(row.valid_time_utc for row in rows) == datetime(2026, 10, 1, 0, tzinfo=UTC)
    assert max(row.valid_time_utc for row in rows) == datetime(2026, 10, 7, 23, tzinfo=UTC)


def test_only_day2_forecasts_are_requested_and_stored(
    open_meteo_previous_runs: dict[str, Any],
) -> None:
    requests: list[httpx.Request] = []

    rows = fetch_previous_runs(make_client(open_meteo_previous_runs, requests), OAKLAND, START, END)

    (request,) = requests
    params = request.url.params
    requested = params["hourly"].split(",")
    assert len(requested) == len(config.WEATHER_VARIABLES)
    assert all(variable.endswith("_previous_day2") for variable in requested)
    assert params["models"] == "gfs_global"
    assert params["timezone"] == "UTC"
    assert (params["start_date"], params["end_date"]) == ("2026-10-01", "2026-10-07")
    assert {row.lead_days for row in rows} == {2}


@pytest.mark.parametrize("lead_days", [0, 1])
def test_leaky_lead_days_are_rejected(lead_days: int) -> None:
    with pytest.raises(ValueError, match="leakage-safe"):
        fetch_previous_runs(make_client({}), OAKLAND, START, END, lead_days=lead_days)


def test_hours_without_a_value_are_omitted() -> None:
    payload = {
        "hourly": {
            "time": ["2024-01-20T11:00", "2024-01-20T12:00"],
            "temperature_2m_previous_day2": [None, 11.5],
        }
    }

    rows = fetch_previous_runs(
        make_client(payload),
        OAKLAND,
        date(2024, 1, 20),
        date(2024, 1, 20),
        variables=["temperature_2m"],
    )

    assert [(row.valid_time_utc, row.value) for row in rows] == [
        (datetime(2024, 1, 20, 12, tzinfo=UTC), 11.5)
    ]
