from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg
import pytest

from gridcast import config
from gridcast.ingest.eia import PERIOD_FORMAT
from gridcast.jobs.ingest import date_chunks, ingest_eia, ingest_weather, month_chunks

API_KEY = "test-key"


class FakeEia:
    """Serves one D and one DF row for every hour in the requested window."""

    def __init__(self) -> None:
        self.demand_mw = 100
        self.windows: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        self.windows.append((params["start"], params["end"]))
        start = datetime.strptime(params["start"], PERIOD_FORMAT)
        end = datetime.strptime(params["end"], PERIOD_FORMAT)
        hours = int((end - start) / timedelta(hours=1)) + 1
        data = [
            {
                "period": (start + timedelta(hours=i)).strftime(PERIOD_FORMAT),
                "respondent": "CISO",
                "type": type_,
                "value": str(value),
            }
            for i in range(hours)
            for type_, value in (("D", self.demand_mw), ("DF", 110))
        ]
        offset, length = int(params["offset"]), int(params["length"])
        page = data[offset : offset + length]
        return httpx.Response(200, json={"response": {"total": str(len(data)), "data": page}})


class FakeOpenMeteo:
    """Serves every requested variable for every hour of the requested dates."""

    def __init__(self) -> None:
        self.temperature = 20.0
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        start = datetime.fromisoformat(params["start_date"])
        end = datetime.fromisoformat(params["end_date"]) + timedelta(hours=23)
        hours = int((end - start) / timedelta(hours=1)) + 1
        hourly = {"time": [(start + timedelta(hours=i)).isoformat() for i in range(hours)]}
        for variable in params["hourly"].split(","):
            hourly[variable] = [self.temperature] * hours
        return httpx.Response(200, json={"hourly": hourly})


def client_for(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        (date(2019, 1, 1), date(2026, 10, 12), 90),
        (date(2026, 10, 5), date(2026, 10, 12), 90),
        (date(2026, 10, 12), date(2026, 10, 12), 1),
    ],
)
def test_date_chunks_cover_the_range_without_gaps_or_overlap(
    start: date, end: date, days: int
) -> None:
    chunks = list(date_chunks(start, end, days))

    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    assert all((chunk_end - chunk_start).days < days for chunk_start, chunk_end in chunks)
    for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
        assert next_start == previous_end + timedelta(days=1)


def test_month_chunks_split_at_month_boundaries() -> None:
    assert list(month_chunks(date(2024, 1, 20), date(2024, 3, 2))) == [
        (date(2024, 1, 20), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]
    assert list(month_chunks(date(2025, 12, 31), date(2026, 1, 1))) == [
        (date(2025, 12, 31), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 1)),
    ]


def test_ingest_eia_twice_yields_the_same_rows(conn: psycopg.Connection) -> None:
    eia = FakeEia()
    start, end = date(2026, 1, 1), date(2026, 7, 1)  # spans several chunks
    hours = ((end - start).days + 1) * 24 + 1  # through midnight after `end`, inclusive

    first = ingest_eia(conn, client_for(eia), API_KEY, start, end, min_interval_s=0)
    second = ingest_eia(conn, client_for(eia), API_KEY, start, end, min_interval_s=0)

    assert len(eia.windows) == 2 * 3
    assert first.changed == 2 * hours
    assert second.changed == 0
    assert count(conn, "raw_eia") == 2 * hours
    periods = conn.execute("select min(period_utc), max(period_utc) from raw_eia").fetchone()
    assert periods == (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 7, 2, tzinfo=UTC))


def test_ingest_eia_overwrites_revised_demand(conn: psycopg.Connection) -> None:
    eia = FakeEia()
    day = date(2026, 10, 9)
    ingest_eia(conn, client_for(eia), API_KEY, day, day, min_interval_s=0)

    eia.demand_mw = 105
    revised = ingest_eia(conn, client_for(eia), API_KEY, day, day, min_interval_s=0)

    assert revised.changed == 25
    assert count(conn, "raw_eia") == 50
    values = conn.execute("select distinct type, value from raw_eia order by type").fetchall()
    assert values == [("D", 105), ("DF", 110)]


def test_ingest_weather_twice_yields_the_same_rows(conn: psycopg.Connection) -> None:
    open_meteo = FakeOpenMeteo()
    start, end = date(2026, 9, 29), date(2026, 10, 2)  # two calendar months
    expected = len(config.LOCATIONS) * 4 * 24  # one row per location and hour

    first = ingest_weather(conn, client_for(open_meteo), start, end, min_interval_s=0)
    second = ingest_weather(conn, client_for(open_meteo), start, end, min_interval_s=0)

    assert len(open_meteo.requests) == 2 * len(config.LOCATIONS) * 2
    assert first.changed == expected
    assert second.changed == 0
    assert count(conn, "raw_weather") == expected
    assert conn.execute("select distinct lead_days, model from raw_weather").fetchall() == [
        (2, "gfs_global")
    ]


def test_ingest_weather_overwrites_revised_forecasts(conn: psycopg.Connection) -> None:
    open_meteo = FakeOpenMeteo()
    day = date(2026, 10, 9)
    oakland = config.LOCATIONS[:1]
    ingest_weather(conn, client_for(open_meteo), day, day, locations=oakland, min_interval_s=0)

    open_meteo.temperature = 25.0
    revised = ingest_weather(
        conn, client_for(open_meteo), day, day, locations=oakland, min_interval_s=0
    )

    assert revised.changed == 24
    assert count(conn, "raw_weather") == 24
    values = conn.execute("select distinct temperature_2m, precipitation from raw_weather")
    assert values.fetchall() == [(25, 25)]
