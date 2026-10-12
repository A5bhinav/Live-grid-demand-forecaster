from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import load_fixture
from tenacity import wait_none

from gridcast import config
from gridcast.ingest import eia
from gridcast.ingest.eia import fetch_region_data
from gridcast.ingest.http import get_json

API_KEY = "test-key"
START = datetime(2026, 10, 9, 0, tzinfo=UTC)
END = datetime(2026, 10, 9, 5, tzinfo=UTC)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def raw_row(period: datetime, value: Any = "100", type_: str = "D") -> dict[str, Any]:
    return {
        "period": period.strftime(eia.PERIOD_FORMAT),
        "respondent": "CISO",
        "type": type_,
        "value": value,
    }


def test_parses_recorded_response(eia_region_data: dict[str, Any]) -> None:
    client = make_client(lambda request: httpx.Response(200, json=eia_region_data))

    rows = fetch_region_data(client, API_KEY, START, END)

    assert len(rows) == 12
    assert [row.type for row in rows].count("D") == 6
    assert [row.type for row in rows].count("DF") == 6
    assert rows[0].period_utc == START
    assert rows[-1].period_utc == END
    assert all(isinstance(row.value, float) for row in rows)


def test_request_matches_eia_contract_and_keeps_key_out_of_url(
    eia_region_data: dict[str, Any],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=eia_region_data)

    fetch_region_data(make_client(handler), API_KEY, START, END)

    (request,) = requests
    params = request.url.params
    assert params["frequency"] == "hourly"
    assert params["facets[respondent][]"] == "CISO"
    assert params.get_list("facets[type][]") == ["D", "DF"]
    assert params["start"] == "2026-10-09T00"
    assert params["end"] == "2026-10-09T05"
    assert request.headers["X-Api-Key"] == API_KEY
    assert API_KEY not in str(request.url)


def test_follows_pagination_past_the_page_cap() -> None:
    total = 12_000
    dataset = [raw_row(START + timedelta(hours=i)) for i in range(total)]
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        offsets.append(offset)
        page = dataset[offset : offset + length]
        # EIA reports `total` as a string.
        return httpx.Response(200, json={"response": {"total": str(total), "data": page}})

    rows = fetch_region_data(make_client(handler), API_KEY, START, END, min_interval_s=0)

    assert offsets == [0, 5000, 10_000]
    assert len(rows) == total
    assert len({row.period_utc for row in rows}) == total


def test_empty_and_null_values_become_missing() -> None:
    data = [raw_row(START, "123"), raw_row(START + timedelta(hours=1), ""), raw_row(END, None)]
    client = make_client(
        lambda request: httpx.Response(200, json={"response": {"total": "3", "data": data}})
    )

    rows = fetch_region_data(client, API_KEY, START, END)

    assert [row.value for row in rows] == [123.0, None, None]


def test_window_is_converted_to_utc(eia_region_data: dict[str, Any]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=eia_region_data)

    local_start = datetime(2026, 10, 8, 17, tzinfo=ZoneInfo("America/Los_Angeles"))
    fetch_region_data(make_client(handler), API_KEY, local_start, END)

    assert requests[0].url.params["start"] == "2026-10-09T00"


def test_naive_datetimes_are_rejected() -> None:
    client = make_client(lambda request: httpx.Response(500))
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_region_data(client, API_KEY, datetime(2026, 10, 9), END)


def test_retries_transient_errors(
    monkeypatch: pytest.MonkeyPatch, eia_region_data: dict[str, Any]
) -> None:
    monkeypatch.setattr(get_json.retry, "wait", wait_none())
    statuses = iter([503, 429, 200])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(next(statuses), json=eia_region_data)

    rows = fetch_region_data(make_client(handler), API_KEY, START, END)

    assert calls == 3
    assert len(rows) == 12


def test_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_json.retry, "wait", wait_none())
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        fetch_region_data(make_client(handler), API_KEY, START, END)

    assert calls == 1
    assert API_KEY not in str(excinfo.value)


def test_config_matches_recorded_eia_metadata() -> None:
    metadata = load_fixture("eia_region_data_metadata.json")["response"]
    type_ids = {facet["id"] for facet in load_fixture("eia_facet_type.json")["response"]["facets"]}

    assert {config.EIA_TYPE_DEMAND, config.EIA_TYPE_OPERATOR_FORECAST} <= type_ids
    assert {frequency["id"] for frequency in metadata["frequency"]} >= {"hourly"}
    assert metadata["startPeriod"] == f"{config.EIA_BACKFILL_START:{eia.PERIOD_FORMAT}}"
