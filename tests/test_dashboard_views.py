from datetime import UTC, datetime, timedelta

import pandas as pd
import psycopg
import pytest
from synthetic import insert_past_forecast, synthetic_sources

from gridcast import config, db, protocol
from gridcast.ingest.eia import EiaRow
from gridcast.jobs import clean as clean_job
from gridcast.jobs import issue, score
from gridcast.jobs.backtest import backtest_report
from gridcast.models.baselines import SeasonalNaive

NOW = datetime.now(UTC)
TODAY = NOW.astimezone(config.LOCAL_TZ).date()
SCORED_DAY = TODAY - timedelta(days=6)
VIEWS = [
    "dashboard_hourly",
    "dashboard_scores",
    "dashboard_backtest",
    "dashboard_flags_monthly",
    "dashboard_status",
]
TABLES = ["raw_eia", "raw_weather", "clean_demand", "forecasts", "scores", "model_versions",
          "backtest_results", "schema_migrations"]  # fmt: skip


@pytest.fixture
def loaded(conn: psycopg.Connection) -> psycopg.Connection:
    """A database after a few weeks of operation: data, a live forecast and a scored day."""
    sources = synthetic_sources(TODAY - timedelta(days=59), days=60)
    demand = sources.raw_demand[
        sources.raw_demand.index <= pd.Timestamp(NOW) - pd.Timedelta(hours=2)
    ]
    demand = demand.copy()
    settled = protocol.target_periods(SCORED_DAY - timedelta(days=1))
    demand[settled[4]] *= 0.5  # one bad actual on the scored day
    rows = [EiaRow("CISO", "D", p.to_pydatetime(), float(v)) for p, v in demand.items()]
    rows += [EiaRow("CISO", "DF", p.to_pydatetime(), float(v) * 1.02) for p, v in demand.items()]
    db.upsert_eia(conn, rows)
    clean_job.run(conn)
    insert_past_forecast(conn, "seasonal_naive-v1", sources.raw_demand.reindex(settled) * 1.05)
    score.run(conn, NOW)
    issue.run(conn, SeasonalNaive(), NOW)
    conn.commit()
    return conn


@pytest.mark.parametrize("view", VIEWS)
def test_the_public_role_can_read_each_dashboard_view(
    loaded: psycopg.Connection, view: str
) -> None:
    loaded.execute("set role anon")
    try:
        loaded.execute(f"select * from {view}").fetchall()
    finally:
        loaded.rollback()
        loaded.execute("reset role")


@pytest.mark.parametrize("table", TABLES)
def test_the_public_role_cannot_read_or_write_tables(
    loaded: psycopg.Connection, table: str
) -> None:
    loaded.execute("set role anon")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            loaded.execute(f"select * from {table} limit 1")
        loaded.rollback()
        loaded.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            loaded.execute(f"delete from {table}")
    finally:
        loaded.rollback()
        loaded.execute("reset role")


def test_hourly_view_joins_forecast_operator_and_unflagged_actuals(
    loaded: psycopg.Connection,
) -> None:
    settled = protocol.target_periods(SCORED_DAY - timedelta(days=1))
    rows = loaded.execute(
        "select period_utc, forecast_mw, operator_mw, actual_mw, actual_flag, model_version "
        "from dashboard_hourly where period_utc = any(%s) order by period_utc",
        (list(settled),),
    ).fetchall()

    assert len(rows) == len(settled)
    assert all(row[1] is not None and row[2] is not None for row in rows)
    assert (rows[4][3], rows[4][4]) == (None, "spike")  # the bad actual is withheld
    assert rows[5][3] is not None and rows[5][4] is None
    assert {row[5] for row in rows} == {"seasonal_naive-v1"}

    tomorrow = loaded.execute(
        "select count(*), count(forecast_mw), count(actual_mw) from dashboard_hourly "
        "where period_utc > %s",
        (protocol.target_periods(TODAY)[0] - pd.Timedelta(hours=1),),
    ).fetchone()
    assert tomorrow == (len(protocol.target_periods(TODAY)),) * 2 + (0,)


def test_status_view_summarises_the_live_record(loaded: psycopg.Connection) -> None:
    status = loaded.execute(
        "select days_forecast, days_scored, latest_scored_date, active_model, "
        "demand_hours_flagged, latest_forecast_target from dashboard_status"
    ).fetchone()

    assert status[:5] == (2, 1, SCORED_DAY, None, 1)
    assert status[5] == protocol.target_periods(TODAY)[-1]


def test_flags_view_counts_flagged_hours_by_local_month(loaded: psycopg.Connection) -> None:
    rows = loaded.execute("select month, flag, hours from dashboard_flags_monthly").fetchall()

    assert rows == [(f"{SCORED_DAY:%Y-%m}", "spike", 1)]


def test_backtest_view_shows_only_the_latest_saved_run(conn: psycopg.Connection) -> None:
    sources = synthetic_sources("2025-06-01", days=150)
    report = backtest_report(
        [SeasonalNaive()], sources, sources.raw_demand * 1.02, [pd.Period("2025-09", freq="M")]
    )
    first = report.results().assign(run_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = report.results().assign(run_at=datetime(2026, 2, 1, tzinfo=UTC))

    assert db.insert_backtest_results(conn, first) == len(first)
    db.insert_backtest_results(conn, second)

    rows = conn.execute(
        "select model, breakdown, key, hours, round(mape, 2), win_rate_vs_operator is null "
        "from dashboard_backtest where breakdown in ('overall', 'month') order by breakdown, model"
    ).fetchall()
    assert rows == [
        ("operator", "month", "2025-09", 720, 2, True),
        ("seasonal_naive", "month", "2025-09", 720, rows[1][4], True),
        ("operator", "overall", "all", 720, 2, True),
        ("seasonal_naive", "overall", "all", 720, rows[3][4], False),
    ]
    assert conn.execute("select count(distinct run_at) from dashboard_backtest").fetchone() == (1,)
    assert conn.execute("select count(distinct run_at) from backtest_results").fetchone() == (2,)
