"""Outside scheduler: have Supabase trigger the GitHub workflows on a timetable.

    uv run python -m gridcast.jobs.scheduler install   # needs GITHUB_DISPATCH_TOKEN
    uv run python -m gridcast.jobs.scheduler status
    uv run python -m gridcast.jobs.scheduler remove

GitHub's own scheduler can delay or drop runs. This is an independent trigger for the same
workflows; see sql/supabase/scheduler.sql. The token only needs permission to run this
repository's workflows: a fine-grained token with "Actions: Read and write" on this one
repository. It is stored in Supabase Vault and nowhere else.
"""

import argparse
from pathlib import Path

import psycopg

from gridcast import config, db

SETUP_SQL = Path(__file__).resolve().parents[3] / "sql" / "supabase" / "scheduler.sql"
SECRET_NAME = "github_dispatch_token"


def install(conn: psycopg.Connection, token: str | None) -> None:
    if not token:
        raise RuntimeError(
            "GITHUB_DISPATCH_TOKEN is not set. Create a fine-grained GitHub token with "
            '"Actions: Read and write" on this repository and put it in .env.'
        )
    existing = conn.execute(
        "select id from vault.secrets where name = %s", (SECRET_NAME,)
    ).fetchone()
    if existing:
        conn.execute("select vault.update_secret(%s, %s)", (existing[0], token))
    else:
        conn.execute(
            "select vault.create_secret(%s, %s, %s)",
            (token, SECRET_NAME, "Runs this repository's GitHub workflows"),
        )
    conn.execute(SETUP_SQL.read_text())


def remove(conn: psycopg.Connection) -> None:
    conn.execute("select cron.unschedule(jobid) from cron.job where jobname like 'gridcast-%'")
    conn.execute("delete from vault.secrets where name = %s", (SECRET_NAME,))


def status(conn: psycopg.Connection) -> None:
    if conn.execute("select to_regclass('cron.job')").fetchone()[0] is None:
        print("not installed")
        return
    for name, schedule, active in conn.execute(
        "select jobname, schedule, active from cron.job where jobname like 'gridcast-%' order by 1"
    ):
        print(f"{name:24s} {schedule:14s} {'active' if active else 'inactive'}")
    print("recent calls to GitHub (kept for about six hours; 204 means accepted):")
    responses = conn.execute(
        "select created, status_code, error_msg from net._http_response "
        "order by created desc limit 8"
    )
    for created, code, error in responses:
        print(f"  {created:%Y-%m-%d %H:%M} UTC  http {code}  {error or ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "status", "remove"])
    args = parser.parse_args()

    settings = config.Settings()
    with db.connect(settings.require_database_url()) as conn:
        if args.action == "install":
            token = settings.github_dispatch_token
            install(conn, token.get_secret_value() if token else None)
            conn.commit()
        elif args.action == "remove":
            remove(conn)
            conn.commit()
        status(conn)


if __name__ == "__main__":
    main()
