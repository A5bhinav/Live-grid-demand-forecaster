"""Postgres access: connections, migrations, and idempotent upserts for the raw tables."""

from collections.abc import Iterable
from pathlib import Path

import psycopg

from gridcast.ingest.eia import EiaRow
from gridcast.ingest.weather import WeatherRow

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "sql" / "migrations"


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply any unapplied files in sql/migrations, in name order. Returns the ones applied."""
    conn.execute(
        "create table if not exists schema_migrations ("
        "name text primary key, applied_at timestamptz not null default now())"
    )
    conn.execute("alter table schema_migrations enable row level security")
    applied = {name for (name,) in conn.execute("select name from schema_migrations")}
    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied


def upsert_eia(conn: psycopg.Connection, rows: Iterable[EiaRow]) -> int:
    """Insert new rows and overwrite revised values. Returns how many were inserted or changed."""
    # Keyed by primary key so a duplicate within one batch cannot break the upsert.
    records = {(row.respondent, row.type, row.period_utc): row.value for row in rows}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "create temp table if not exists staging_eia ("
            "respondent text, type text, period_utc timestamptz, value numeric) on commit drop"
        )
        cur.execute("truncate staging_eia")
        with cur.copy("copy staging_eia from stdin") as copy:
            for key, value in records.items():
                copy.write_row((*key, value))
        cur.execute(
            """
            insert into raw_eia (respondent, type, period_utc, value)
            select respondent, type, period_utc, value from staging_eia
            on conflict (respondent, type, period_utc) do update
              set value = excluded.value, updated_at = now()
              where raw_eia.value is distinct from excluded.value
            """
        )
        return cur.rowcount


def upsert_weather(conn: psycopg.Connection, rows: Iterable[WeatherRow]) -> int:
    """Insert new rows and overwrite revised values. Returns how many were inserted or changed."""
    records = {
        (row.location, row.valid_time_utc, row.lead_days, row.model, row.variable): row.value
        for row in rows
    }
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "create temp table if not exists staging_weather ("
            "location text, valid_time_utc timestamptz, lead_days int, model text, "
            "variable text, value numeric) on commit drop"
        )
        cur.execute("truncate staging_weather")
        with cur.copy("copy staging_weather from stdin") as copy:
            for key, value in records.items():
                copy.write_row((*key, value))
        cur.execute(
            """
            insert into raw_weather (location, valid_time_utc, lead_days, model, variable, value)
            select location, valid_time_utc, lead_days, model, variable, value
            from staging_weather
            on conflict (location, valid_time_utc, lead_days, model, variable) do update
              set value = excluded.value, updated_at = now()
              where raw_weather.value is distinct from excluded.value
            """
        )
        return cur.rowcount
