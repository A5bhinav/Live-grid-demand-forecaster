import json
import os
import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from gridcast import db

FIXTURES = Path(__file__).parent / "fixtures"
# The local database from docker-compose.yml; CI points this at its Postgres service.
DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:54322/gridcast"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tests must not touch the network; use recorded fixtures")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def eia_region_data() -> dict[str, Any]:
    """Recorded EIA response: CISO D and DF for 2026-10-09T00 through T05."""
    return load_fixture("eia_region_data_ciso.json")


@pytest.fixture
def open_meteo_previous_runs() -> dict[str, Any]:
    """Recorded Open-Meteo response: Oakland, gfs_global, day-2, 2026-10-01 through 10-07."""
    return load_fixture("open_meteo_previous_runs_oakland.json")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """A migrated database in a throwaway schema, dropped after the test."""
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    try:
        connection = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres is not reachable (run `docker compose up -d`): {exc}")
    schema = f"gridcast_test_{uuid.uuid4().hex[:12]}"
    connection.execute(f"create schema {schema}")
    connection.execute(f"set search_path to {schema}")
    connection.commit()
    try:
        db.migrate(connection)
        connection.commit()
        yield connection
    finally:
        connection.rollback()
        connection.execute(f"drop schema {schema} cascade")
        connection.commit()
        connection.close()
