from datetime import UTC, date, datetime, timedelta

import pandas as pd
import psycopg
import pytest
from test_screen import synthetic_demand

from gridcast import config, db, protocol
from gridcast.ingest.eia import EiaRow
from gridcast.jobs import issue, score
from gridcast.models.baselines import SeasonalNaive

# The database stamps real time on every forecast, so these tests are anchored to today.
NOW = datetime.now(UTC)
TODAY = NOW.astimezone(config.LOCAL_TZ).date()
SETTLED_DAY = TODAY - timedelta(days=6)
RECENT_DAY = TODAY - timedelta(days=1)
VERSION = "test-model-v1"


def published_demand(days: int = 60) -> pd.Series:
    start = (NOW - timedelta(days=days - 1)).date()
    demand = synthetic_demand(start=str(start), days=days)
    return demand[demand.index <= pd.Timestamp(NOW) - pd.Timedelta(hours=2)]


def load_raw(conn: psycopg.Connection, demand: pd.Series, operator: pd.Series) -> None:
    rows = [EiaRow("CISO", "D", period.to_pydatetime(), value) for period, value in demand.items()]
    rows += [
        EiaRow("CISO", "DF", period.to_pydatetime(), value) for period, value in operator.items()
    ]
    db.upsert_eia(conn, rows)
    conn.commit()


def insert_past_forecast(conn: psycopg.Connection, forecast: pd.Series) -> None:
    """Seed a forecast for a day that has already happened, which the table forbids."""
    conn.execute("alter table forecasts disable trigger forecasts_stamp_issue")
    for period, value in forecast.items():
        conn.execute(
            "insert into forecasts (issued_at, target_time_utc, model_version, yhat_mw) "
            "values (%s, %s, %s, %s)",
            (period - pd.Timedelta(days=2), period.to_pydatetime(), VERSION, float(value)),
        )
    conn.execute("alter table forecasts enable trigger forecasts_stamp_issue")
    conn.commit()


def target_periods(target_day: date) -> pd.DatetimeIndex:
    return protocol.target_periods(target_day - timedelta(days=1))


def future_hour() -> datetime:
    return (pd.Timestamp(NOW).floor("h") + pd.Timedelta(hours=30)).to_pydatetime()


def insert_one(conn: psycopg.Connection, target: datetime, issued_at: str | None = None) -> None:
    conn.execute(
        "insert into forecasts (issued_at, target_time_utc, model_version, yhat_mw) "
        "values (coalesce(%s::timestamptz, now()), %s, %s, 25000)",
        (issued_at, target, VERSION),
    )


@pytest.mark.parametrize(
    "statement",
    [
        "update forecasts set yhat_mw = yhat_mw + 1",
        "update forecasts set issued_at = now() - interval '1 day'",
        "delete from forecasts",
        "truncate forecasts",
    ],
)
def test_forecasts_cannot_be_changed_or_removed(conn: psycopg.Connection, statement: str) -> None:
    insert_one(conn, future_hour())
    conn.commit()

    with pytest.raises(psycopg.errors.RaiseException, match="forecasts are immutable"):
        conn.execute(statement)
    conn.rollback()

    assert conn.execute("select yhat_mw from forecasts").fetchall() == [(25000,)]


def test_issue_time_comes_from_the_database_clock(conn: psycopg.Connection) -> None:
    insert_one(conn, future_hour(), issued_at="2020-01-01T00:00:00Z")
    conn.commit()

    (issued_at,) = conn.execute("select issued_at from forecasts").fetchone()
    assert abs(issued_at - datetime.now(UTC)) < timedelta(minutes=5)


@pytest.mark.parametrize("hours_ahead", [1, 0, -48])
def test_a_forecast_for_an_hour_that_has_started_is_rejected(
    conn: psycopg.Connection, hours_ahead: int
) -> None:
    # The period ending one hour from the top of this hour is already under way.
    target = pd.Timestamp(NOW).floor("h") + pd.Timedelta(hours=hours_ahead)

    with pytest.raises(psycopg.errors.RaiseException, match="issued after that hour started"):
        insert_one(conn, target.to_pydatetime(), issued_at="2020-01-01T00:00:00Z")
    conn.rollback()

    assert conn.execute("select count(*) from forecasts").fetchone() == (0,)


def test_issue_job_records_tomorrow_exactly_once(conn: psycopg.Connection) -> None:
    demand = published_demand()
    load_raw(conn, demand, demand * 1.02)
    targets = protocol.target_periods(TODAY)

    first = issue.run(conn, SeasonalNaive(), NOW)
    conn.commit()
    second = issue.run(conn, SeasonalNaive(), NOW)
    conn.commit()

    assert first.inserted == len(targets)
    assert second.inserted == 0
    stored = conn.execute(
        "select target_time_utc, model_version, yhat_mw from forecasts order by target_time_utc"
    ).fetchall()
    assert [row[0] for row in stored] == list(targets)
    assert {row[1] for row in stored} == {"seasonal_naive-v1"}
    assert [float(row[2]) for row in stored] == pytest.approx(first.forecast.tolist())
    assert conn.execute(
        "select max(issued_at) < min(target_time_utc) from forecasts"
    ).fetchone() == (True,)


def test_issue_job_dry_run_records_nothing(conn: psycopg.Connection) -> None:
    demand = published_demand()
    load_raw(conn, demand, demand)

    stats = issue.run(conn, SeasonalNaive(), NOW, dry_run=True)

    assert stats.forecast.notna().all()
    assert stats.inserted == 0
    assert conn.execute("select count(*) from forecasts").fetchone() == (0,)


def test_issue_job_fails_rather_than_record_a_partial_forecast(conn: psycopg.Connection) -> None:
    demand = published_demand(days=4)  # nothing from a week ago to copy
    load_raw(conn, demand, demand)

    with pytest.raises(RuntimeError, match="has no value for"):
        issue.run(conn, SeasonalNaive(), NOW)

    assert conn.execute("select count(*) from forecasts").fetchone() == (0,)


def test_score_job_scores_settled_days_once(conn: psycopg.Connection) -> None:
    demand = published_demand()
    load_raw(conn, demand, demand * 1.02)
    settled, recent = target_periods(SETTLED_DAY), target_periods(RECENT_DAY)
    insert_past_forecast(conn, demand.reindex(settled) * 1.05)
    insert_past_forecast(conn, demand.reindex(recent) * 1.05)

    first = score.run(conn, NOW)
    conn.commit()
    second = score.run(conn, NOW)

    assert [(row["model_version"], row["target_date"]) for row in first] == [(VERSION, SETTLED_DAY)]
    assert second == []
    (row,) = conn.execute(
        "select hours, hours_scored, other_hours_scored, mape, operator_mape, "
        "other_hours_mape, operator_other_hours_mape, peak_error_mw, peak_hours_off "
        "from scores"
    ).fetchall()
    hours = len(settled)
    assert row[:3] == (hours, hours, hours - 8)
    assert [float(value) for value in row[3:7]] == pytest.approx([5.0, 2.0, 5.0, 2.0])
    assert float(row[7]) == pytest.approx(demand.reindex(settled).max() * 0.05)
    assert row[8] == 0


def test_score_job_skips_flagged_actuals_and_hours_the_operator_missed(
    conn: psycopg.Connection,
) -> None:
    demand = published_demand()
    settled = target_periods(SETTLED_DAY)
    reported = demand.copy()
    reported[settled[5]] *= 0.55  # a spike in the reported actual
    load_raw(conn, reported, (demand * 1.02).drop(settled[12]))
    insert_past_forecast(conn, demand.reindex(settled) * 1.05)

    (row,) = score.run(conn, NOW)

    assert row["hours"] == len(settled)
    assert row["hours_scored"] == len(settled) - 2
    assert row["mape"] == pytest.approx(5.0)
    assert row["peak_error_mw"] is None  # not a complete day


def test_a_day_with_no_operator_forecast_is_recorded_as_unscored(
    conn: psycopg.Connection,
) -> None:
    demand = published_demand()
    settled = target_periods(SETTLED_DAY)
    load_raw(conn, demand, (demand * 1.02).drop(settled))
    insert_past_forecast(conn, demand.reindex(settled) * 1.05)

    (row,) = score.run(conn, NOW)
    conn.commit()

    assert (row["hours_scored"], row["other_hours_scored"]) == (0, 0)
    assert "mape" not in row
    assert conn.execute("select hours_scored, mape from scores").fetchall() == [(0, None)]
    assert score.run(conn, NOW) == []


class OtherModel(SeasonalNaive):
    version = "other-model-v1"


def test_a_newly_promoted_model_cannot_add_a_second_forecast_for_the_same_day(
    conn: psycopg.Connection,
) -> None:
    demand = published_demand()
    load_raw(conn, demand, demand)
    issue.run(conn, SeasonalNaive(), NOW)
    conn.commit()

    second = issue.run(conn, OtherModel(), NOW)

    assert (second.inserted, second.already_issued) == (0, True)
    versions = conn.execute("select distinct model_version from forecasts").fetchall()
    assert versions == [("seasonal_naive-v1",)]
