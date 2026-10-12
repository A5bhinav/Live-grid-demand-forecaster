import psycopg
from test_screen import synthetic_demand

from gridcast import db
from gridcast.ingest.eia import EiaRow
from gridcast.jobs.clean import run

SPIKE, EMPTY, ABSENT = 350, 500, 600


def load_raw(conn: psycopg.Connection, *, spike_factor: float = 0.55) -> None:
    demand = synthetic_demand(days=30).round()
    rows = []
    for position, (period, value) in enumerate(demand.items()):
        if position == ABSENT:
            continue
        if position == SPIKE:
            value = round(value * spike_factor)
        rows.append(
            EiaRow("CISO", "D", period.to_pydatetime(), None if position == EMPTY else value)
        )
        # The operator forecast must never reach clean_demand.
        rows.append(EiaRow("CISO", "DF", period.to_pydatetime(), 1.0))
    db.upsert_eia(conn, rows)
    conn.commit()


def test_clean_job_writes_one_row_per_hour_with_flags(conn: psycopg.Connection) -> None:
    load_raw(conn)

    stats = run(conn)
    conn.commit()

    assert (stats.hours, stats.flagged, stats.imputed, stats.changed) == (720, 3, 3, 720)
    rows = conn.execute(
        "select flag, imputed, demand_mw > 15000 from clean_demand "
        "where flag is not null order by period_utc"
    ).fetchall()
    assert rows == [("spike", True, True), ("missing", True, True), ("missing", True, True)]
    assert conn.execute("select count(*), min(demand_mw) > 15000 from clean_demand").fetchone() == (
        720,
        True,
    )


def test_clean_job_is_idempotent_and_follows_revisions(conn: psycopg.Connection) -> None:
    load_raw(conn)
    run(conn)
    conn.commit()

    assert run(conn).changed == 0
    conn.commit()

    load_raw(conn, spike_factor=1.0)  # EIA corrects the bad hour
    revised = run(conn)

    assert (revised.flagged, revised.changed) == (2, 1)
    assert conn.execute("select count(*) from clean_demand").fetchone() == (720,)
