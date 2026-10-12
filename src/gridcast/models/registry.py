"""Model registry: artifacts in object storage, versions and the active flag in Postgres."""

import os
import subprocess
from pathlib import Path
from typing import Protocol

import httpx
import psycopg
from psycopg.types.json import Jsonb

from gridcast import config
from gridcast.backtest.rolling import Forecaster
from gridcast.models.baselines import SeasonalNaive
from gridcast.models.gbm import GbmForecaster

LOCAL_ARTIFACTS = Path(__file__).resolve().parents[3] / "artifacts" / "models"


class ArtifactStore(Protocol):
    def put(self, path: str, content: bytes) -> None: ...

    def get(self, path: str) -> bytes: ...


class LocalStore:
    """Artifacts on disk, for development without a Supabase project."""

    def __init__(self, root: Path = LOCAL_ARTIFACTS) -> None:
        self.root = root

    def put(self, path: str, content: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def get(self, path: str) -> bytes:
        return (self.root / path).read_bytes()


class SupabaseStore:
    """Artifacts in a private Supabase Storage bucket, written with the secret key."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        client: httpx.Client | None = None,
        bucket: str = config.MODEL_BUCKET,
    ) -> None:
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.client = client or httpx.Client(timeout=60)
        self.headers = {"apikey": secret_key, "Authorization": f"Bearer {secret_key}"}

    def ensure_bucket(self) -> None:
        response = self.client.post(
            f"{self.base}/bucket",
            headers=self.headers,
            json={"id": self.bucket, "name": self.bucket, "public": False},
        )
        already_exists = response.status_code in (400, 409) and "exist" in response.text.lower()
        if not already_exists:
            response.raise_for_status()

    def put(self, path: str, content: bytes) -> None:
        self.ensure_bucket()
        response = self.client.post(
            f"{self.base}/object/{self.bucket}/{path}",
            headers={**self.headers, "Content-Type": "application/json", "x-upsert": "true"},
            content=content,
        )
        response.raise_for_status()

    def get(self, path: str) -> bytes:
        response = self.client.get(f"{self.base}/object/{self.bucket}/{path}", headers=self.headers)
        response.raise_for_status()
        return response.content


def artifact_store(settings: config.Settings) -> ArtifactStore:
    if settings.supabase_url and settings.supabase_service_role_key:
        return SupabaseStore(
            settings.supabase_url, settings.supabase_service_role_key.get_secret_value()
        )
    return LocalStore()


def git_sha() -> str:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def register(
    conn: psycopg.Connection,
    store: ArtifactStore,
    forecaster: GbmForecaster,
    *,
    backtest: dict[str, object],
    activate: bool,
) -> None:
    """Record a trained model. With `activate`, it replaces the active version."""
    path = None
    if activate:
        path = f"{forecaster.version}.json"
        store.put(path, forecaster.to_artifact())
    params = {"kind": "gbm", "feature_set": forecaster.feature_set, "rounds": forecaster.rounds}
    with conn.transaction():
        if activate:
            conn.execute("update model_versions set is_active = false where is_active")
        conn.execute(
            "insert into model_versions (version, git_sha, train_start, train_end, params, "
            "backtest, artifact_path, is_active) values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                forecaster.version,
                git_sha(),
                config.TRAIN_START,
                forecaster.train_end,
                Jsonb({**params, "lightgbm": forecaster.params}),
                Jsonb(backtest),
                path,
                activate,
            ),
        )


def active_forecaster(conn: psycopg.Connection, store: ArtifactStore) -> Forecaster:
    """The model the issue job should use: the active version, or the baseline if none."""
    row = conn.execute(
        "select version, artifact_path from model_versions where is_active"
    ).fetchone()
    if row is None:
        return SeasonalNaive()
    version, path = row
    return GbmForecaster.from_artifact(store.get(path), version)
