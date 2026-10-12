from datetime import date

import numpy as np
import pandas as pd
import pytest

from gridcast import protocol, scoring
from gridcast.features.build import Sources
from gridcast.jobs.backtest import backtest_report
from gridcast.models.baselines import SeasonalNaive

DAY_ONE = date(2026, 7, 14)  # issue date; targets are July 15


def frame_for(days: int = 2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Forecasts and cleaned actuals for consecutive target days, actual flat at 20,000 MW."""
    periods = pd.DatetimeIndex(
        [
            period
            for offset in range(days)
            for period in protocol.target_periods(DAY_ONE + pd.Timedelta(days=offset))
        ],
        name="period_utc",
    )
    cleaned = pd.DataFrame({"demand_mw": 20_000.0, "flag": None, "imputed": False}, index=periods)
    forecasts = pd.DataFrame({"model": 21_000.0, "operator": 19_000.0}, index=periods)
    return forecasts, cleaned


def test_metrics_match_hand_computed_values() -> None:
    forecasts, cleaned = frame_for(days=1)
    forecasts.iloc[0, 0] = 24_000.0  # one hour 4,000 MW high; the other 23 are 1,000 high

    frame = scoring.score_frame(forecasts, cleaned)
    metrics = scoring.hourly_metrics(frame, ["model", "operator"]).set_index("model")

    assert metrics.loc["model", "hours"] == 24
    assert metrics.loc["model", "mae_mw"] == pytest.approx((4_000 + 23 * 1_000) / 24)
    assert metrics.loc["model", "mape"] == pytest.approx((20 + 23 * 5) / 24)
    assert metrics.loc["model", "rmse_mw"] == pytest.approx(np.sqrt((16e6 + 23 * 1e6) / 24))
    assert metrics.loc["operator", "mape"] == pytest.approx(5.0)


def test_flagged_hours_are_never_scored_even_when_filled_in() -> None:
    forecasts, cleaned = frame_for(days=1)
    cleaned.iloc[3, cleaned.columns.get_loc("flag")] = "spike"
    cleaned.iloc[3, cleaned.columns.get_loc("imputed")] = True
    cleaned = cleaned.drop(cleaned.index[10])  # never published

    frame = scoring.score_frame(forecasts, cleaned)

    assert frame["skipped"].value_counts().to_dict() == {"actual_flagged": 2}
    assert scoring.hourly_metrics(frame, ["model"])["hours"].item() == 22


def test_an_hour_missing_from_one_forecast_is_skipped_for_all() -> None:
    forecasts, cleaned = frame_for(days=1)
    forecasts.iloc[5, forecasts.columns.get_loc("operator")] = np.nan
    forecasts.iloc[6, forecasts.columns.get_loc("model")] = np.nan

    frame = scoring.score_frame(forecasts, cleaned)
    metrics = scoring.hourly_metrics(frame, ["model", "operator"])

    assert frame["skipped"].value_counts().to_dict() == {"operator_missing": 1, "model_missing": 1}
    assert metrics["hours"].tolist() == [22, 22]


def test_peak_error_and_timing_use_complete_days_only() -> None:
    forecasts, cleaned = frame_for(days=2)
    day_one = protocol.target_periods(DAY_ONE)
    cleaned.loc[day_one[17], "demand_mw"] = 30_000.0  # actual peak
    forecasts.loc[day_one[19], "model"] = 28_500.0  # forecast peak, two hours late
    day_two = protocol.target_periods(DAY_ONE + pd.Timedelta(days=1))
    cleaned.loc[day_two[0], "flag"] = "missing"  # day two is incomplete

    daily = scoring.daily_metrics(scoring.score_frame(forecasts, cleaned), ["model"])
    first, second = daily.to_dict("records")

    assert first["peak_error_mw"] == pytest.approx(-1_500.0)
    assert first["peak_hours_off"] == 2.0
    assert second["hours"] == 23
    assert np.isnan(second["peak_error_mw"])


def test_win_rate_counts_days_the_model_beats_the_operator() -> None:
    forecasts, cleaned = frame_for(days=4)
    last_day = protocol.target_periods(DAY_ONE + pd.Timedelta(days=3))
    forecasts.loc[last_day, "model"] = 20_100.0  # the only day the model is closer
    forecasts["operator"] = 19_500.0

    daily = scoring.daily_metrics(scoring.score_frame(forecasts, cleaned), ["model", "operator"])

    assert scoring.win_rate(daily, "model") == 25.0


def test_calendar_keys_use_the_local_day_of_each_hour() -> None:
    forecasts, cleaned = frame_for(days=1)

    frame = scoring.score_frame(forecasts, cleaned)

    assert set(frame["target_date"]) == {date(2026, 7, 15)}
    assert set(frame["month"]) == {"2026-07"}
    assert set(frame["season"]) == {"summer"}
    assert (frame["time_of_day"] == "09:00-17:00").sum() == 8


def test_backtest_report_scores_baseline_and_operator_on_the_same_hours() -> None:
    from test_screen import synthetic_demand

    raw_demand = synthetic_demand(start="2025-06-01", days=150)
    operator = raw_demand * 1.02
    operator = operator.drop(operator.index[2500:2524])  # the operator skips a day
    months = [pd.Period("2025-09", freq="M")]

    report = backtest_report([SeasonalNaive()], Sources(raw_demand=raw_demand), operator, months)
    overall = report.overall()

    assert report.models == ["seasonal_naive", "operator"]
    assert overall["hours"].tolist() == [30 * 24 - 24] * 2
    assert overall.loc["operator", "mape"] == pytest.approx(2.0)
    assert overall.loc["seasonal_naive", "mape"] < 2.0  # the synthetic series repeats weekly
    assert report.skipped().to_dict() == {"scored": 696, "operator_missing": 24}
    assert list(report.mape_by("month").index) == ["2025-09"]
