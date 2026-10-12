"""Postgres access: connections, migrations, and idempotent upserts for the raw tables."""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

from gridcast import config
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
    """Insert new rows and overwrite revised values. Returns how many were inserted or changed.

    A variable the source no longer returns keeps its stored value.
    """
    variables = config.WEATHER_VARIABLES
    records = {
        (row.location, row.valid_time_utc, row.lead_days, row.model): [
            row.values.get(variable) for variable in variables
        ]
        for row in rows
    }
    columns = sql.SQL(", ").join(map(sql.Identifier, variables))
    merged = sql.SQL(", ").join(
        sql.SQL("coalesce(excluded.{0}, raw_weather.{0})").format(sql.Identifier(variable))
        for variable in variables
    )
    stored = sql.SQL(", ").join(
        sql.SQL("raw_weather.{}").format(sql.Identifier(variable)) for variable in variables
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "create temp table if not exists staging_weather on commit drop as "
                "select location, valid_time_utc, lead_days, model, {columns} "
                "from raw_weather with no data"
            ).format(columns=columns)
        )
        cur.execute("truncate staging_weather")
        with cur.copy("copy staging_weather from stdin") as copy:
            for key, values in records.items():
                copy.write_row((*key, *values))
        cur.execute(
            sql.SQL(
                """
                insert into raw_weather (location, valid_time_utc, lead_days, model, {columns})
                select location, valid_time_utc, lead_days, model, {columns}
                from staging_weather
                on conflict (location, valid_time_utc, lead_days, model) do update
                  set ({columns}) = row({merged}), updated_at = now()
                  where ({stored}) is distinct from ({merged})
                """
            ).format(columns=columns, merged=merged, stored=stored)
        )
        return cur.rowcount


def read_raw_eia(conn: psycopg.Connection, type_: str) -> pd.Series:
    """One EIA series in MW, indexed by hour-ending UTC period."""
    rows = conn.execute(
        "select period_utc, value from raw_eia where respondent = %s and type = %s "
        "order by period_utc",
        (config.EIA_RESPONDENT, type_),
    ).fetchall()
    index = pd.DatetimeIndex(
        pd.to_datetime([period for period, _ in rows], utc=True), name="period_utc"
    )
    values = [None if value is None else float(value) for _, value in rows]
    return pd.Series(values, index=index, dtype=float)


def read_raw_demand(conn: psycopg.Connection) -> pd.Series:
    """Reported actual demand in MW, indexed by hour-ending UTC period."""
    return read_raw_eia(conn, config.EIA_TYPE_DEMAND).rename("demand_mw")


def upsert_clean_demand(conn: psycopg.Connection, cleaned: pd.DataFrame) -> int:
    """Write the output of `clean`. Returns how many rows were inserted or changed."""
    demand = cleaned["demand_mw"].round(1)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "create temp table if not exists staging_clean_demand ("
            "period_utc timestamptz, demand_mw numeric, flag text, imputed boolean) "
            "on commit drop"
        )
        cur.execute("truncate staging_clean_demand")
        with cur.copy("copy staging_clean_demand from stdin") as copy:
            for period, value, flag, imputed in zip(
                cleaned.index, demand, cleaned["flag"], cleaned["imputed"], strict=True
            ):
                copy.write_row(
                    (
                        period.to_pydatetime(),
                        None if pd.isna(value) else float(value),
                        None if pd.isna(flag) else str(flag),
                        bool(imputed),
                    )
                )
        cur.execute(
            """
            insert into clean_demand (period_utc, demand_mw, flag, imputed)
            select period_utc, demand_mw, flag, imputed from staging_clean_demand
            on conflict (period_utc) do update
              set demand_mw = excluded.demand_mw, flag = excluded.flag,
                  imputed = excluded.imputed, updated_at = now()
              where (clean_demand.demand_mw, clean_demand.flag, clean_demand.imputed)
                is distinct from (excluded.demand_mw, excluded.flag, excluded.imputed)
            """
        )
        return cur.rowcount


def insert_forecasts(conn: psycopg.Connection, model_version: str, forecast: pd.Series) -> int:
    """Record a forecast. Hours that already have one for this model are left untouched.

    The database stamps `issued_at` itself and refuses hours that have already started.
    """
    rows = [
        (period.to_pydatetime(), model_version, float(value)) for period, value in forecast.items()
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into forecasts (target_time_utc, model_version, yhat_mw) "
            "values (%s, %s, %s) on conflict (model_version, target_time_utc) do nothing",
            rows,
        )
        return cur.rowcount


def read_unscored_forecasts(conn: psycopg.Connection) -> pd.DataFrame:
    """Forecast rows whose local target day has no row in `scores` yet."""
    rows = conn.execute(
        """
        select f.model_version, f.target_time_utc, f.yhat_mw
        from forecasts f
        where not exists (
          select 1 from scores s
          where s.model_version = f.model_version
            and s.target_date
              = ((f.target_time_utc - interval '1 hour') at time zone %s)::date
        )
        order by f.model_version, f.target_time_utc
        """,
        (config.LOCAL_TZ.key,),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["model_version", "period_utc", "yhat_mw"])
    frame["period_utc"] = pd.to_datetime(frame["period_utc"], utc=True)
    frame["yhat_mw"] = frame["yhat_mw"].astype(float)
    return frame


def insert_scores(conn: psycopg.Connection, scores: Iterable[dict[str, object]]) -> int:
    """Record daily scores. A day that is already scored is left untouched."""
    inserted = 0
    for score in scores:
        columns = sql.SQL(", ").join(map(sql.Identifier, score))
        values = sql.SQL(", ").join(sql.Placeholder() * len(score))
        result = conn.execute(
            sql.SQL(
                "insert into scores ({}) values ({}) "
                "on conflict (model_version, target_date) do nothing"
            ).format(columns, values),
            list(score.values()),
        )
        inserted += result.rowcount
    return inserted


def read_raw_weather(conn: psycopg.Connection, since: datetime | None = None) -> pd.DataFrame:
    """Archived weather forecasts: one row per location, valid time, lead and model."""
    variables = config.WEATHER_VARIABLES
    rows = conn.execute(
        sql.SQL(
            "select location, valid_time_utc, lead_days, model, {} from raw_weather "
            "where valid_time_utc >= coalesce(%s, '-infinity'::timestamptz) "
            "order by valid_time_utc, location"
        ).format(sql.SQL(", ").join(map(sql.Identifier, variables))),
        (since,),
    ).fetchall()
    frame = pd.DataFrame(
        rows, columns=["location", "valid_time_utc", "lead_days", "model", *variables]
    )
    frame["valid_time_utc"] = pd.to_datetime(frame["valid_time_utc"], utc=True)
    frame[list(variables)] = frame[list(variables)].astype(float)
    return frame


def read_live_forecasts(conn: psycopg.Connection, since: datetime) -> pd.Series:
    """What was actually issued: the first forecast recorded for each hour, any model."""
    rows = conn.execute(
        "select distinct on (target_time_utc) target_time_utc, yhat_mw from forecasts "
        "where target_time_utc >= %s order by target_time_utc, issued_at",
        (since,),
    ).fetchall()
    index = pd.DatetimeIndex(
        pd.to_datetime([period for period, _ in rows], utc=True), name="period_utc"
    )
    return pd.Series([float(value) for _, value in rows], index=index, dtype=float)


def insert_backtest_results(conn: psycopg.Connection, results: pd.DataFrame) -> int:
    """Store one backtest run for the dashboard. `results` has the table's columns."""
    columns = list(results.columns)
    statement = sql.SQL("insert into backtest_results ({}) values ({})").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    rows = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in results.astype(object).itertuples(index=False, name=None)
    ]
    with conn.cursor() as cur:
        cur.executemany(statement, rows)
        return cur.rowcount
