from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import psycopg
import pytest
from synthetic import load_into_database, synthetic_sources

from gridcast import alerts, config, protocol
from gridcast.features.build import Sources
from gridcast.jobs import clean as clean_job
from gridcast.jobs import issue, monitor
from gridcast.models.baselines import SeasonalNaive

NOW = datetime.now(UTC)
TODAY = NOW.astimezone(config.LOCAL_TZ).date()
NOON = datetime.combine(TODAY, datetime.min.time(), tzinfo=config.LOCAL_TZ) + timedelta(hours=12)
EARLY = datetime.combine(TODAY, datetime.min.time(), tzinfo=config.LOCAL_TZ) + timedelta(hours=8)


def sources_through_now() -> Sources:
    everything = synthetic_sources(TODAY - timedelta(days=40), days=44)
    published = everything.raw_demand.index <= pd.Timestamp(NOW) - pd.Timedelta(hours=2)
    return Sources(raw_demand=everything.raw_demand[published], weather=everything.weather)


@pytest.fixture
def healthy(conn: psycopg.Connection) -> psycopg.Connection:
    load_into_database(conn, sources_through_now())
    clean_job.run(conn)
    issue.run(conn, SeasonalNaive(), NOW)
    conn.commit()
    return conn


def results(conn: psycopg.Connection, now: datetime) -> dict[str, bool]:
    return {check.name: check.ok for check in monitor.run(conn, now)}


def test_a_healthy_pipeline_passes_every_check(healthy: psycopg.Connection) -> None:
    assert results(healthy, NOW) == {
        "forecast issued": True,
        "demand complete": True,
        "flagged share": True,
        "weather complete": True,
        "performance": True,
        "outside scheduler": True,
    }


def test_a_missing_forecast_alerts_only_after_it_is_due(conn: psycopg.Connection) -> None:
    load_into_database(conn, sources_through_now())
    clean_job.run(conn)

    assert monitor.forecast_issued(conn, EARLY).ok
    late = monitor.forecast_issued(conn, NOON)
    assert not late.ok
    assert late.detail.startswith("0 of ")


def test_missing_demand_hours_are_reported(healthy: psycopg.Connection) -> None:
    gap = pd.Timestamp(NOW).floor("h") - pd.Timedelta(hours=10)
    healthy.execute("delete from raw_eia where period_utc = %s", (gap,))

    check = monitor.demand_complete(healthy, NOW)

    assert not check.ok
    assert check.detail == "1 of the last 24 due hours missing"


def test_a_burst_of_flagged_demand_is_reported(healthy: psycopg.Connection) -> None:
    healthy.execute(
        "update clean_demand set flag = 'spike' "
        "where period_utc > (select max(period_utc) from clean_demand) - interval '12 hours'"
    )

    assert not monitor.flagged_share(healthy).ok


def test_partial_weather_for_tomorrow_is_reported(healthy: psycopg.Connection) -> None:
    hour = protocol.target_periods(TODAY)[3]
    healthy.execute(
        "update raw_weather set cloud_cover = null "
        "where valid_time_utc = %s and location = 'fresno'",
        (hour,),
    )

    check = monitor.weather_complete(healthy, NOW)

    assert not check.ok
    assert check.detail.startswith(f"{len(protocol.target_periods(TODAY)) * 6 - 1} of ")


@pytest.mark.parametrize(("live_mape", "ok"), [(4.0, True), (6.0, False)])
def test_performance_alerts_when_live_error_exceeds_the_backtest_by_half(
    conn: psycopg.Connection, live_mape: float, ok: bool
) -> None:
    conn.execute(
        "insert into backtest_results (run_at, first_month, last_month, model, breakdown, key, "
        "hours, mape) values (now(), '2025-10', '2026-09', 'gbm_full', 'overall', 'all', 8710, 3.4)"
    )
    for offset in range(5):
        conn.execute(
            "insert into scores (model_version, target_date, hours, hours_scored, "
            "other_hours_scored, mape) values ('m', %s, 24, 24, 16, %s)",
            (TODAY - timedelta(days=offset + 4), live_mape),
        )

    assert monitor.performance(conn).ok is ok


def test_performance_is_not_judged_on_too_few_days(conn: psycopg.Connection) -> None:
    check = monitor.performance(conn)

    assert check.ok
    assert "too few" in check.detail


@pytest.mark.parametrize(
    ("url", "payload"),
    [
        ("https://hooks.slack.com/services/T/B/x", {"text": "forecast missing"}),
        ("https://discord.com/api/webhooks/1/x", {"content": "forecast missing"}),
    ],
)
def test_alerts_use_the_payload_each_service_expects(url: str, payload: dict[str, str]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    sent = alerts.send_alert(
        "forecast missing", url, httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert sent
    assert len(requests) == 1
    assert str(requests[0].url) == url
    assert httpx.Response(200, content=requests[0].content).json() == payload


def test_no_alert_is_sent_without_a_webhook() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert not alerts.send_alert("forecast missing", None, client)


@pytest.mark.parametrize(
    ("responses", "ok", "detail"),
    [
        ([(204, None), (204, None)], True, "0 of 2 recent calls refused"),
        ([(204, None), (401, None)], False, "1 of 2 recent calls refused"),
        ([(None, "timeout")], False, "1 of 1 recent calls refused"),
    ],
)
def test_refused_calls_from_the_outside_scheduler_are_reported(
    conn: psycopg.Connection, responses: list[tuple[int | None, str | None]], ok: bool, detail: str
) -> None:
    # Stands in for pg_net's response log, which only exists on Supabase.
    conn.execute("create schema net")
    conn.execute("create table net._http_response (status_code int, error_msg text)")
    try:
        for response in responses:
            conn.execute("insert into net._http_response values (%s, %s)", response)

        check = monitor.outside_scheduler(conn)
    finally:
        conn.execute("drop schema net cascade")

    assert (check.ok, check.detail) == (ok, detail)


def test_installing_the_outside_scheduler_requires_a_token(conn: psycopg.Connection) -> None:
    from gridcast.jobs import scheduler

    with pytest.raises(RuntimeError, match="GITHUB_DISPATCH_TOKEN is not set"):
        scheduler.install(conn, None)
