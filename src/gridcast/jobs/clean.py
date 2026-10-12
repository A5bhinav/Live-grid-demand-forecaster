"""Clean job: screen reported demand and rebuild the `clean_demand` table.

    uv run python -m gridcast.jobs.clean

The whole history is re-screened each run, because a flag can depend on later hours.
"""

import logging
from dataclasses import dataclass

import psycopg

from gridcast import config, db
from gridcast.clean.screen import clean

log = logging.getLogger("gridcast.clean")


@dataclass(frozen=True)
class CleanStats:
    hours: int
    flagged: int
    imputed: int
    changed: int  # rows inserted or updated in clean_demand


def run(conn: psycopg.Connection) -> CleanStats:
    cleaned = clean(db.read_raw_demand(conn))
    changed = db.upsert_clean_demand(conn, cleaned)
    return CleanStats(
        hours=len(cleaned),
        flagged=int(cleaned["flag"].notna().sum()),
        imputed=int(cleaned["imputed"].sum()),
        changed=changed,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with db.connect(config.Settings().require_database_url()) as conn:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        stats = run(conn)
    log.info(
        "clean hours=%d flagged=%d imputed=%d changed=%d",
        stats.hours,
        stats.flagged,
        stats.imputed,
        stats.changed,
    )


if __name__ == "__main__":
    main()
