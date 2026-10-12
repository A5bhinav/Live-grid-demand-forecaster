"""Rolling-origin backtest: refit at the start of each month, then forecast every day in it.

Each day is forecast exactly as the live job would, through `build_inputs`.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from gridcast import config, protocol
from gridcast.features.build import IssueInputs, Sources, build_inputs


class Forecaster(Protocol):
    name: str  # column name in backtests
    version: str  # recorded on live forecast rows

    def fit(self, sources: Sources, first_issue_date: date) -> None:
        """Train using only what `build_inputs` exposes for issue dates before this one."""

    def predict(self, inputs: IssueInputs) -> pd.Series:
        """Forecast MW for `inputs.targets`."""


def settled_months(last_period: pd.Timestamp, count: int) -> list[pd.Period]:
    """The most recent `count` local months whose every day is old enough to score."""
    settled = last_period - pd.Timedelta(hours=config.SCORING_DELAY_HOURS)
    # The month holding the first period that is not yet settled is the incomplete one.
    unsettled = pd.DatetimeIndex([settled + protocol.HOUR])
    current = protocol.local_hour_start(unsettled)[0].tz_localize(None)
    last_full = current.to_period("M") - 1
    return list(pd.period_range(end=last_full, periods=count, freq="M"))


def run_backtest(
    forecasters: Sequence[Forecaster], sources: Sources, months: Sequence[pd.Period]
) -> pd.DataFrame:
    """Forecasts for every hour of `months`: one row per period, one column per forecaster."""
    days = []
    for month in months:
        target_days = [stamp.date() for stamp in pd.date_range(month.start_time, month.end_time)]
        for forecaster in forecasters:
            forecaster.fit(sources, target_days[0] - timedelta(days=1))
        for target_day in target_days:
            inputs = build_inputs(target_day - timedelta(days=1), sources)
            days.append(
                pd.concat([forecaster.predict(inputs) for forecaster in forecasters], axis=1)
            )
    return pd.concat(days)
