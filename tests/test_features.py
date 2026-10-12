from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from test_screen import synthetic_demand

from gridcast import protocol
from gridcast.backtest.rolling import run_backtest, settled_months
from gridcast.features.build import Sources, build_inputs
from gridcast.models.baselines import SeasonalNaive

START = date(2025, 6, 1)


@pytest.fixture
def raw_demand() -> pd.Series:
    return synthetic_demand(start=str(START), days=200)  # spans the autumn transition


def random_issue_dates(count: int) -> list[date]:
    rng = np.random.default_rng(7)
    return [START + timedelta(days=int(day)) for day in rng.integers(30, 190, size=count)]


@pytest.mark.parametrize("issue_date", random_issue_dates(12))
def test_no_input_is_later_than_the_demand_cutoff(raw_demand: pd.Series, issue_date: date) -> None:
    inputs = build_inputs(issue_date, Sources(raw_demand=raw_demand))

    cutoff = protocol.demand_cutoff(issue_date)
    assert inputs.demand.index.max() == cutoff
    assert cutoff < protocol.issue_time(issue_date) < inputs.targets.min()


@pytest.mark.parametrize("issue_date", random_issue_dates(12))
def test_nothing_after_the_cutoff_can_change_inputs_or_forecasts(
    raw_demand: pd.Series, issue_date: date
) -> None:
    cutoff = protocol.demand_cutoff(issue_date)
    tampered = raw_demand.copy()
    tampered[tampered.index > cutoff] = 999_999.0
    truncated = raw_demand[raw_demand.index <= cutoff]

    reference = build_inputs(issue_date, Sources(raw_demand=raw_demand))
    for variant in (tampered, truncated):
        inputs = build_inputs(issue_date, Sources(raw_demand=variant))
        pd.testing.assert_series_equal(inputs.demand, reference.demand)
        pd.testing.assert_series_equal(
            SeasonalNaive().predict(inputs), SeasonalNaive().predict(reference)
        )


def test_hours_not_yet_published_at_the_cutoff_are_filled_in(raw_demand: pd.Series) -> None:
    issue_date = date(2025, 9, 10)
    cutoff = protocol.demand_cutoff(issue_date)
    late = raw_demand[raw_demand.index <= cutoff - pd.Timedelta(hours=5)]

    inputs = build_inputs(issue_date, Sources(raw_demand=late))

    assert inputs.demand.index.max() == cutoff
    assert inputs.demand.notna().all()
    error = (inputs.demand - raw_demand).dropna().abs()
    assert error.max() < 800


def test_inputs_fail_loudly_when_no_demand_is_available(raw_demand: pd.Series) -> None:
    with pytest.raises(ValueError, match="no demand available"):
        build_inputs(date(2024, 1, 1), Sources(raw_demand=raw_demand))


def test_seasonal_naive_repeats_the_same_local_hour_last_week(raw_demand: pd.Series) -> None:
    issue_date = date(2025, 8, 12)
    inputs = build_inputs(issue_date, Sources(raw_demand=raw_demand))

    forecast = SeasonalNaive().predict(inputs)

    assert list(forecast.index) == list(protocol.target_periods(issue_date))
    expected = raw_demand.reindex(forecast.index - pd.Timedelta(hours=168)).to_numpy()
    np.testing.assert_allclose(forecast.to_numpy(), expected)


@pytest.mark.parametrize(
    ("issue_date", "hours"), [(date(2025, 11, 1), 25), (date(2025, 11, 8), 24)]
)
def test_seasonal_naive_covers_every_hour_around_the_autumn_transition(
    raw_demand: pd.Series, issue_date: date, hours: int
) -> None:
    forecast = SeasonalNaive().predict(build_inputs(issue_date, Sources(raw_demand=raw_demand)))

    assert len(forecast) == hours
    assert forecast.notna().all()


def test_settled_months_stop_before_a_month_too_recent_to_score() -> None:
    def months(last_period: str) -> list[str]:
        return [str(month) for month in settled_months(pd.Timestamp(last_period, tz="UTC"), 3)]

    assert months("2026-10-12 08:00") == ["2026-07", "2026-08", "2026-09"]
    # 72 hours after local midnight ending September is 2026-10-04 07:00 UTC.
    assert months("2026-10-04 07:00") == ["2026-07", "2026-08", "2026-09"]
    assert months("2026-10-04 06:00") == ["2026-06", "2026-07", "2026-08"]


class Recorder(SeasonalNaive):
    name = "recorder"

    def __init__(self) -> None:
        self.fitted: list[date] = []

    def fit(self, sources: Sources, first_issue_date: date) -> None:
        self.fitted.append(first_issue_date)


def test_backtest_refits_each_month_and_forecasts_every_hour(raw_demand: pd.Series) -> None:
    recorder = Recorder()
    months = list(pd.period_range("2025-10", "2025-11", freq="M"))

    forecasts = run_backtest([SeasonalNaive(), recorder], Sources(raw_demand=raw_demand), months)

    assert recorder.fitted == [date(2025, 9, 30), date(2025, 10, 31)]
    assert list(forecasts.columns) == ["seasonal_naive", "recorder"]
    assert len(forecasts) == (31 + 30) * 24 + 1  # November 2 has 25 hours
    assert forecasts.index.is_unique
    assert forecasts.index.is_monotonic_increasing
    assert forecasts.notna().all().all()


def test_a_backtest_day_does_not_depend_on_that_day_or_later(raw_demand: pd.Series) -> None:
    months = [pd.Period("2025-10", freq="M")]
    target_day = date(2025, 10, 20)
    tampered = raw_demand.copy()
    tampered[tampered.index > protocol.demand_cutoff(target_day - timedelta(days=1))] *= 3

    honest = run_backtest([SeasonalNaive()], Sources(raw_demand=raw_demand), months)
    cheated = run_backtest([SeasonalNaive()], Sources(raw_demand=tampered), months)

    targets = protocol.target_periods(target_day - timedelta(days=1))
    pd.testing.assert_frame_equal(honest.loc[targets], cheated.loc[targets])
    assert not honest.loc[targets[-1] + pd.Timedelta(days=9) :].equals(
        cheated.loc[targets[-1] + pd.Timedelta(days=9) :]
    )
