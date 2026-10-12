from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import psycopg
import pytest
from synthetic import insert_past_forecast, load_into_database, synthetic_sources

from gridcast import config, protocol
from gridcast.features.build import Sources, build_inputs
from gridcast.jobs import issue, retrain
from gridcast.models import registry
from gridcast.models.baselines import SeasonalNaive
from gridcast.models.gbm import GbmForecaster

# Promotion looks at recent settled days and the live record, so this is anchored to today.
NOW = datetime.now(UTC)
TODAY = NOW.astimezone(config.LOCAL_TZ).date()
START = TODAY - timedelta(days=150)
FAST = {"learning_rate": 0.1, "num_threads": 2}


@pytest.fixture(scope="module")
def sources() -> Sources:
    everything = synthetic_sources(START, days=154)  # weather runs a few days ahead
    published = everything.raw_demand.index <= pd.Timestamp(NOW) - pd.Timedelta(hours=2)
    return Sources(raw_demand=everything.raw_demand[published], weather=everything.weather)


@pytest.fixture(autouse=True)
def _small_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRAIN_START", START + timedelta(days=20))
    monkeypatch.setattr(config, "GATE_DAYS", 7)


@pytest.fixture(scope="module")
def trained(sources: Sources) -> GbmForecaster:
    model = GbmForecaster("full", version="gbm_full-test-1", params=FAST)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "TRAIN_START", START + timedelta(days=20))
        model.fit(sources, TODAY)
    return model


def test_local_store_round_trip(tmp_path: Path) -> None:
    store = registry.LocalStore(tmp_path)

    store.put("gbm/v1.json", b"model bytes")

    assert store.get("gbm/v1.json") == b"model bytes"


def test_supabase_store_writes_to_a_private_bucket_with_the_secret_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/bucket"):
            return httpx.Response(400, json={"message": "The resource already exists"})
        return httpx.Response(200, content=b"stored" if request.method == "GET" else b"{}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = registry.SupabaseStore("https://project.supabase.co/", "sb_secret_test", client)

    store.put("v1.json", b"model bytes")
    content = store.get("v1.json")

    bucket, upload, download = requests
    assert bucket.url.path == "/storage/v1/bucket"
    assert b'"public":false' in bucket.content.replace(b" ", b"")
    assert (upload.method, upload.url.path) == ("POST", "/storage/v1/object/models/v1.json")
    assert upload.content == b"model bytes"
    assert upload.headers["x-upsert"] == "true"
    assert (download.method, download.url.path) == ("GET", "/storage/v1/object/models/v1.json")
    assert content == b"stored"
    assert all(request.headers["Authorization"] == "Bearer sb_secret_test" for request in requests)
    assert all("sb_secret_test" not in str(request.url) for request in requests)


def test_supabase_store_raises_when_storage_refuses() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    store = registry.SupabaseStore("https://project.supabase.co", "sb_secret_test", client)

    with pytest.raises(httpx.HTTPStatusError):
        store.put("v1.json", b"model bytes")


def test_the_baseline_is_used_when_no_version_is_active(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    forecaster = registry.active_forecaster(conn, registry.LocalStore(tmp_path))

    assert isinstance(forecaster, SeasonalNaive)


def test_an_activated_model_is_what_the_issue_job_loads(
    conn: psycopg.Connection, tmp_path: Path, sources: Sources, trained: GbmForecaster
) -> None:
    store = registry.LocalStore(tmp_path)

    registry.register(conn, store, trained, backtest={"gate": {"passed": True}}, activate=True)
    conn.commit()
    loaded = registry.active_forecaster(conn, store)

    inputs = build_inputs(TODAY, sources)
    assert loaded.version == "gbm_full-test-1"
    np.testing.assert_allclose(loaded.predict(inputs), trained.predict(inputs))
    row = conn.execute(
        "select is_active, artifact_path, params->>'feature_set', backtest->'gate'->>'passed', "
        "train_end from model_versions"
    ).fetchone()
    assert row == (True, "gbm_full-test-1.json", "full", "true", trained.train_end)


def test_activating_a_version_retires_the_previous_one(
    conn: psycopg.Connection, tmp_path: Path, trained: GbmForecaster
) -> None:
    store = registry.LocalStore(tmp_path)
    newer = GbmForecaster.from_artifact(trained.to_artifact(), version="gbm_full-test-2")
    rejected = GbmForecaster.from_artifact(trained.to_artifact(), version="gbm_full-test-3")

    registry.register(conn, store, trained, backtest={}, activate=True)
    registry.register(conn, store, newer, backtest={}, activate=True)
    registry.register(conn, store, rejected, backtest={}, activate=False)
    conn.commit()

    rows = conn.execute(
        "select version, is_active, artifact_path is not null from model_versions order by version"
    ).fetchall()
    assert rows == [
        ("gbm_full-test-1", False, True),
        ("gbm_full-test-2", True, True),
        ("gbm_full-test-3", False, False),
    ]
    assert registry.active_forecaster(conn, store).version == "gbm_full-test-2"


def gate_days() -> list[date]:
    settled = pd.Timestamp(NOW) - pd.Timedelta(hours=config.SCORING_DELAY_HOURS)
    last = protocol.local_hour_start(pd.DatetimeIndex([settled]))[0].date() - timedelta(days=1)
    return [last - timedelta(days=offset) for offset in reversed(range(7))]


def test_gate_passes_a_model_that_beats_the_baseline_when_nothing_was_issued(
    sources: Sources,
) -> None:
    result = retrain.gate(sources, pd.Series(dtype=float), NOW)

    days = gate_days()
    assert (result.first_day, result.last_day) == (str(days[0]), str(days[-1]))
    assert result.live_hours == 0
    assert result.hours == sum(
        len(protocol.target_periods(day - timedelta(days=1))) for day in days
    )
    assert result.candidate_mape < result.live_record_mape
    assert result.passed


def test_gate_rejects_a_model_worse_than_what_was_actually_issued(sources: Sources) -> None:
    perfect_record = sources.raw_demand  # the live forecasts happened to be exactly right

    result = retrain.gate(sources, perfect_record, NOW)

    assert result.live_hours == result.hours
    assert result.live_record_mape == 0
    assert not result.passed


def test_gate_never_lets_the_candidate_train_on_the_days_it_is_judged_on(
    sources: Sources,
) -> None:
    first_judged = protocol.target_periods(gate_days()[0] - timedelta(days=1))[0]
    demand = sources.raw_demand.copy()
    demand[demand.index >= first_judged] *= 1.4  # only the judged days change

    honest = retrain.gate(sources, pd.Series(dtype=float), NOW)
    shifted = retrain.gate(
        Sources(raw_demand=demand, weather=sources.weather), pd.Series(dtype=float), NOW
    )

    # A candidate that had seen those days would have followed the jump.
    assert shifted.candidate_mape > honest.candidate_mape + 15


def test_retrain_promotes_a_passing_model_and_the_issue_job_uses_it(
    conn: psycopg.Connection, tmp_path: Path, sources: Sources
) -> None:
    store = registry.LocalStore(tmp_path)
    load_into_database(conn, sources)

    version, result = retrain.run(conn, store, NOW)
    conn.commit()
    stats = issue.run(conn, registry.active_forecaster(conn, store), NOW)
    conn.commit()

    assert result.passed
    assert version.startswith(f"gbm_full-{NOW:%Y%m%dT%H%M}-")
    assert conn.execute("select version, is_active from model_versions").fetchall() == [
        (version, True)
    ]
    assert stats.inserted == len(protocol.target_periods(TODAY))
    assert stats.hours_without_weather == 0
    assert conn.execute("select distinct model_version from forecasts").fetchall() == [(version,)]


def test_retrain_records_but_does_not_promote_a_failing_model(
    conn: psycopg.Connection, tmp_path: Path, sources: Sources
) -> None:
    store = registry.LocalStore(tmp_path)
    load_into_database(conn, sources)
    judged = pd.DatetimeIndex(
        [p for day in gate_days() for p in protocol.target_periods(day - timedelta(days=1))]
    )
    insert_past_forecast(conn, "seasonal_naive-v1", sources.raw_demand.reindex(judged))

    version, result = retrain.run(conn, store, NOW)
    conn.commit()

    assert not result.passed
    assert conn.execute(
        "select version, is_active, artifact_path, backtest->'gate'->>'passed' from model_versions"
    ).fetchall() == [(version, False, None, "false")]
    assert list(tmp_path.iterdir()) == []
    assert isinstance(registry.active_forecaster(conn, store), SeasonalNaive)
