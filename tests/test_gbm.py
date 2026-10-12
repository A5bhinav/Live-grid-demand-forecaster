from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from synthetic import synthetic_sources

from gridcast import config, protocol
from gridcast.backtest.rolling import run_backtest
from gridcast.features.build import (
    FEATURE_SETS,
    Sources,
    build_inputs,
    cached_features,
    feature_frame,
)
from gridcast.models.baselines import SeasonalNaive
from gridcast.models.gbm import GbmForecaster, training_set

START = date(2025, 5, 1)
ORIGIN = date(2025, 9, 30)  # first issue date of the held-out month
FAST = {"learning_rate": 0.1, "num_threads": 2}


@pytest.fixture(scope="module")
def sources() -> Sources:
    return synthetic_sources(START, days=190)


@pytest.fixture(autouse=True)
def _train_from_synthetic_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRAIN_START", START + timedelta(days=20))


def tampered(sources: Sources, after: pd.Timestamp) -> Sources:
    """The same sources with everything after `after` replaced by absurd values."""
    demand = sources.raw_demand.copy()
    demand[demand.index > after] = 999_999.0
    weather = sources.weather.copy()
    late = weather["valid_time_utc"] > after
    weather.loc[late, list(config.WEATHER_VARIABLES)] = 500.0
    return Sources(raw_demand=demand, weather=weather)


def test_feature_frame_has_every_declared_feature_for_each_target(sources: Sources) -> None:
    features = feature_frame(build_inputs(date(2025, 8, 12), sources))

    assert list(features.index) == list(protocol.target_periods(date(2025, 8, 12)))
    assert set(FEATURE_SETS["full"]) == set(features.columns)
    assert features.notna().all().all()
    assert set(FEATURE_SETS["calendar_lags"]) < set(FEATURE_SETS["plus_temperature"])
    assert set(FEATURE_SETS["plus_temperature"]) < set(FEATURE_SETS["full"])


def test_weather_inputs_are_day2_forecasts_no_later_than_the_last_target(sources: Sources) -> None:
    issue_date = date(2025, 8, 12)
    leaky = sources.weather.copy()
    leaky["lead_days"] = 1
    leaky[list(config.WEATHER_VARIABLES)] = 500.0
    other_model = leaky.assign(lead_days=2, model="best_match")
    polluted = Sources(
        raw_demand=sources.raw_demand,
        weather=pd.concat([sources.weather, leaky, other_model], ignore_index=True),
    )

    clean_inputs = build_inputs(issue_date, sources)
    inputs = build_inputs(issue_date, polluted)

    pd.testing.assert_frame_equal(inputs.weather, clean_inputs.weather)
    assert inputs.weather.index.max() == inputs.targets[-1]
    assert inputs.demand.index.max() == protocol.demand_cutoff(issue_date)


@pytest.mark.parametrize("issue_date", [date(2025, 7, 3), date(2025, 9, 14), date(2025, 10, 31)])
def test_features_ignore_demand_after_the_cutoff_and_weather_after_the_targets(
    sources: Sources, issue_date: date
) -> None:
    targets = protocol.target_periods(issue_date)
    late_weather = tampered(sources, after=targets[-1])
    late_demand = Sources(
        raw_demand=tampered(sources, after=protocol.demand_cutoff(issue_date)).raw_demand,
        weather=late_weather.weather,
    )

    expected = feature_frame(build_inputs(issue_date, sources))

    pd.testing.assert_frame_equal(feature_frame(build_inputs(issue_date, late_demand)), expected)


def test_backtest_and_live_paths_build_identical_features(sources: Sources) -> None:
    issue_date = date(2025, 8, 20)
    from_training = cached_features(issue_date, sources)  # what a backtest trains on
    fresh = Sources(raw_demand=sources.raw_demand.copy(), weather=sources.weather.copy())

    live = feature_frame(build_inputs(issue_date, fresh))  # what the issue job computes

    pd.testing.assert_frame_equal(from_training, live)


def test_holiday_features_follow_the_target_day(sources: Sources) -> None:
    independence_day = feature_frame(build_inputs(date(2025, 7, 3), sources))
    day_before = feature_frame(build_inputs(date(2025, 7, 2), sources))

    assert independence_day["is_holiday"].eq(1).all()
    assert day_before["is_holiday"].eq(0).all()
    assert day_before["holiday_tomorrow"].eq(1).all()
    assert independence_day["is_weekend"].eq(0).all()  # a Friday


def test_training_rows_end_at_the_origins_demand_cutoff(sources: Sources) -> None:
    data = training_set(sources, ORIGIN)

    assert data.features.index.max() == protocol.demand_cutoff(ORIGIN)
    assert data.features.index.min() == protocol.target_periods(config.TRAIN_START)[0]
    assert data.target.notna().all()


def test_training_never_learns_from_flagged_hours(sources: Sources) -> None:
    spiked = sources.raw_demand.copy()
    bad_hour = protocol.target_periods(date(2025, 8, 1))[10]
    spiked[bad_hour] *= 0.5

    data = training_set(Sources(raw_demand=spiked, weather=sources.weather), ORIGIN)

    assert bad_hour not in data.target.index
    assert len(data.target) == len(training_set(sources, ORIGIN).target) - 1


def test_fit_ignores_everything_after_the_origin(sources: Sources) -> None:
    inputs = build_inputs(ORIGIN, sources)
    honest = GbmForecaster("full", params=FAST)
    honest.fit(sources, ORIGIN)

    cheat = GbmForecaster("full", params=FAST)
    cheat.fit(tampered(sources, after=protocol.demand_cutoff(ORIGIN)), ORIGIN)

    assert cheat.rounds == honest.rounds
    np.testing.assert_allclose(cheat.predict(inputs), honest.predict(inputs))


def test_model_with_weather_beats_seasonal_naive_and_the_calendar_only_model(
    sources: Sources,
) -> None:
    models = [
        SeasonalNaive(),
        GbmForecaster("calendar_lags", params=FAST),
        GbmForecaster("full", params=FAST),
    ]
    month = [pd.Period("2025-10", freq="M")]

    forecasts = run_backtest(models, sources, month)

    actual = sources.raw_demand.reindex(forecasts.index)
    mape = forecasts.sub(actual, axis=0).abs().div(actual, axis=0).mean() * 100
    assert mape["gbm_full"] < mape["gbm_calendar_lags"] < mape["seasonal_naive"]
    assert mape["gbm_full"] < 2.0


def test_artifact_round_trip_gives_identical_forecasts(sources: Sources) -> None:
    model = GbmForecaster("plus_temperature", version="gbm-test-1", params=FAST)
    model.fit(sources, ORIGIN)
    inputs = build_inputs(ORIGIN, sources)

    restored = GbmForecaster.from_artifact(model.to_artifact(), version="gbm-test-1")

    assert restored.version == "gbm-test-1"
    assert restored.columns == list(FEATURE_SETS["plus_temperature"])
    assert restored.train_end == model.train_end
    np.testing.assert_allclose(restored.predict(inputs), model.predict(inputs))


def test_predicting_before_fitting_is_an_error(sources: Sources) -> None:
    with pytest.raises(RuntimeError, match="has not been fitted"):
        GbmForecaster("full").predict(build_inputs(ORIGIN, sources))
