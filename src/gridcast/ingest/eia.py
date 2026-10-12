"""EIA-930 hourly region data: actual demand (`D`) and the operator's forecast (`DF`)."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from gridcast import config
from gridcast.ingest.http import get_json

PERIOD_FORMAT = "%Y-%m-%dT%H"


@dataclass(frozen=True)
class EiaRow:
    respondent: str
    type: str
    period_utc: datetime  # hour-ending: T05 covers 04:00-05:00
    value: float | None  # MWh; None when EIA publishes an empty value


def _parse_row(raw: dict[str, Any]) -> EiaRow:
    value = raw.get("value")
    return EiaRow(
        respondent=raw["respondent"],
        type=raw["type"],
        period_utc=datetime.strptime(raw["period"], PERIOD_FORMAT).replace(tzinfo=UTC),
        # Values arrive as strings, and empty strings mean missing.
        value=None if value in (None, "") else float(value),
    )


def _format_period(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    return moment.astimezone(UTC).strftime(PERIOD_FORMAT)


def fetch_region_data(
    client: httpx.Client,
    api_key: str,
    start: datetime,
    end: datetime,
    *,
    respondent: str = config.EIA_RESPONDENT,
    types: Sequence[str] = (config.EIA_TYPE_DEMAND, config.EIA_TYPE_OPERATOR_FORECAST),
    page_size: int = config.EIA_PAGE_SIZE,
    min_interval_s: float = config.EIA_MIN_REQUEST_INTERVAL_S,
) -> list[EiaRow]:
    """Fetch every hourly row with `start <= period <= end`, following pagination."""
    base_params: list[tuple[str, str | int]] = [
        ("frequency", "hourly"),
        ("data[]", "value"),
        ("facets[respondent][]", respondent),
        *(("facets[type][]", type_) for type_ in types),
        ("start", _format_period(start)),
        ("end", _format_period(end)),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("length", page_size),
    ]
    # The key goes in a header, not the query string, so it never appears in URLs
    # that httpx logs or embeds in exception messages.
    headers = {"X-Api-Key": api_key}
    rows: list[EiaRow] = []
    while True:
        params = [*base_params, ("offset", len(rows))]
        page = get_json(client, config.EIA_REGION_DATA_URL, params, headers)["response"]
        rows.extend(_parse_row(raw) for raw in page["data"])
        if not page["data"] or len(rows) >= int(page["total"]):
            return rows
        time.sleep(min_interval_s)
