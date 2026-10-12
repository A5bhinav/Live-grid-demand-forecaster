"""Retrain job: train a candidate, judge it against the live record, promote it if it holds up.

    uv run python -m gridcast.jobs.retrain

The gate: a copy of the candidate trained only on data before the last 28 settled days
forecasts those days. Its MAPE must not exceed that of what was actually issued on the
same hours by more than the tolerance. Days with no live forecast count the seasonal-naive
baseline instead. Only then is a model trained on everything and made active.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import psycopg

from gridcast import config, db, protocol, scoring
from gridcast.clean.screen import clean
from gridcast.features.build import Sources, build_inputs
from gridcast.jobs.backtest import load_sources
from gridcast.models import registry
from gridcast.models.baselines import SeasonalNaive
from gridcast.models.gbm import GbmForecaster

log = logging.getLogger("gridcast.retrain")
CANDIDATE, LIVE = "candidate", "live_record"


@dataclass(frozen=True)
class GateResult:
    first_day: str
    last_day: str
    hours: int
    live_hours: int  # scored hours where a real live forecast existed
    candidate_mape: float
    live_record_mape: float
    tolerance: float
    passed: bool


def gate(sources: Sources, live_forecasts: pd.Series, now: datetime) -> GateResult:
    settled = pd.Timestamp(now) - pd.Timedelta(hours=config.SCORING_DELAY_HOURS)
    last_day = protocol.local_hour_start(pd.DatetimeIndex([settled]))[0].date() - timedelta(days=1)
    days = [last_day - timedelta(days=offset) for offset in reversed(range(config.GATE_DAYS))]

    candidate = GbmForecaster("full", name=CANDIDATE)
    candidate.fit(sources, days[0] - timedelta(days=1))
    baseline = SeasonalNaive()
    forecasts = []
    for day in days:
        inputs = build_inputs(day - timedelta(days=1), sources)
        forecasts.append(
            pd.DataFrame({CANDIDATE: candidate.predict(inputs), LIVE: baseline.predict(inputs)})
        )
    frame = pd.concat(forecasts)
    issued = live_forecasts.reindex(frame.index)
    frame[LIVE] = issued.fillna(frame[LIVE])

    scored = scoring.score_frame(frame, clean(sources.raw_demand))
    mape = scoring.hourly_metrics(scored, [CANDIDATE, LIVE]).set_index("model")["mape"]
    kept = scored["skipped"].isna()
    return GateResult(
        first_day=str(days[0]),
        last_day=str(days[-1]),
        hours=int(kept.sum()),
        live_hours=int((kept & issued.notna()).sum()),
        candidate_mape=float(mape[CANDIDATE]),
        live_record_mape=float(mape[LIVE]),
        tolerance=config.PROMOTION_TOLERANCE,
        passed=bool(mape[CANDIDATE] <= mape[LIVE] * (1 + config.PROMOTION_TOLERANCE)),
    )


def run(
    conn: psycopg.Connection, store: registry.ArtifactStore, now: datetime
) -> tuple[str, GateResult]:
    sources = load_sources(conn)
    result = gate(
        sources, db.read_live_forecasts(conn, pd.Timestamp(now) - pd.Timedelta(days=60)), now
    )

    version = f"gbm_full-{now:%Y%m%dT%H%M}-{registry.git_sha()[:7]}"
    final = GbmForecaster("full", version=version)
    if result.passed:
        # Everything known today: the same data a forecast issued today could use.
        final.fit(sources, now.astimezone(config.LOCAL_TZ).date())
    registry.register(conn, store, final, backtest={"gate": asdict(result)}, activate=result.passed)
    return version, result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = config.Settings()
    with db.connect(settings.require_database_url()) as conn:
        for name in db.migrate(conn):
            log.info("applied migration %s", name)
        version, result = run(conn, registry.artifact_store(settings), datetime.now(UTC))
    log.info(
        "gate %s..%s hours=%d live_hours=%d candidate_mape=%.2f live_record_mape=%.2f",
        result.first_day,
        result.last_day,
        result.hours,
        result.live_hours,
        result.candidate_mape,
        result.live_record_mape,
    )
    log.info("%s %s", version, "promoted" if result.passed else "not promoted")


if __name__ == "__main__":
    main()
