from datetime import UTC, datetime, timedelta

import psycopg

from gridcast import db
from gridcast.ingest.eia import EiaRow
from gridcast.ingest.weather import WeatherRow

T0 = datetime(2026, 10, 9, 0, tzinfo=UTC)


def eia_rows(count: int, value: float | None = 100.0) -> list[EiaRow]:
    return [EiaRow("CISO", "D", T0 + timedelta(hours=i), value) for i in range(count)]


def weather_rows(count: int, value: float = 20.0) -> list[WeatherRow]:
    return [
        WeatherRow("oakland", T0 + timedelta(hours=i), 2, "gfs_global", "temperature_2m", value)
        for i in range(count)
    ]


def count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_migrate_is_idempotent(conn: psycopg.Connection) -> None:
    assert db.migrate(conn) == []
    assert count(conn, "raw_eia") == 0
    assert count(conn, "raw_weather") == 0


def test_every_table_has_row_level_security(conn: psycopg.Connection) -> None:
    unprotected = conn.execute(
        "select relname from pg_class "
        "where relnamespace = current_schema()::regnamespace and relkind = 'r' "
        "and not relrowsecurity"
    ).fetchall()
    assert unprotected == []


def test_upsert_eia_is_idempotent(conn: psycopg.Connection) -> None:
    assert db.upsert_eia(conn, eia_rows(48)) == 48
    conn.commit()
    assert db.upsert_eia(conn, eia_rows(48)) == 0
    conn.commit()
    assert count(conn, "raw_eia") == 48


def test_upsert_eia_overwrites_revised_values(conn: psycopg.Connection) -> None:
    db.upsert_eia(conn, eia_rows(3))
    conn.commit()

    revised = [EiaRow("CISO", "D", T0, 250.0)]
    assert db.upsert_eia(conn, revised) == 1
    conn.commit()

    rows = conn.execute(
        "select value, updated_at > ingested_at from raw_eia order by period_utc"
    ).fetchall()
    assert rows == [(250, True), (100, False), (100, False)]


def test_upsert_eia_fills_in_a_value_that_was_missing(conn: psycopg.Connection) -> None:
    db.upsert_eia(conn, eia_rows(1, value=None))
    conn.commit()
    assert conn.execute("select value from raw_eia").fetchone() == (None,)

    assert db.upsert_eia(conn, eia_rows(1, value=42502.0)) == 1
    assert conn.execute("select value from raw_eia").fetchone() == (42502,)


def test_upsert_eia_tolerates_duplicates_within_a_batch(conn: psycopg.Connection) -> None:
    batch = [EiaRow("CISO", "D", T0, 1.0), EiaRow("CISO", "D", T0, 2.0)]

    assert db.upsert_eia(conn, batch) == 1
    assert conn.execute("select value from raw_eia").fetchone() == (2,)


def test_upserts_can_repeat_within_one_transaction(conn: psycopg.Connection) -> None:
    assert db.upsert_eia(conn, eia_rows(2)) == 2
    assert db.upsert_eia(conn, eia_rows(4)) == 2
    assert db.upsert_weather(conn, weather_rows(2)) == 2
    assert db.upsert_weather(conn, weather_rows(4)) == 2


def test_upsert_weather_is_idempotent_and_overwrites_revisions(conn: psycopg.Connection) -> None:
    assert db.upsert_weather(conn, weather_rows(24)) == 24
    conn.commit()
    assert db.upsert_weather(conn, weather_rows(24)) == 0
    conn.commit()

    assert db.upsert_weather(conn, weather_rows(1, value=31.5)) == 1
    conn.commit()

    assert count(conn, "raw_weather") == 24
    assert conn.execute(
        "select value from raw_weather where valid_time_utc = %s", (T0,)
    ).fetchone() == (31.5,)
