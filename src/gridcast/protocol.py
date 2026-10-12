"""The forecast protocol: when a forecast is issued, what it targets and what it may see.

Periods are EIA's hour-ending UTC timestamps. A period belongs to the local day in which
its hour starts, so the day's last period is stamped at the next local midnight.
"""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from gridcast import config

HOUR = pd.Timedelta(hours=1)


def _local(day: date, hour: int = 0) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, time(hour)), tz=config.LOCAL_TZ)


def issue_time(issue_date: date) -> pd.Timestamp:
    """The simulated moment a forecast is issued on `issue_date`."""
    return _local(issue_date, config.ISSUE_HOUR_LOCAL).tz_convert("UTC")


def demand_cutoff(issue_date: date) -> pd.Timestamp:
    """The last period whose demand a forecast issued on `issue_date` may use."""
    return _local(issue_date, config.DEMAND_CUTOFF_HOUR_LOCAL).tz_convert("UTC")


def target_periods(issue_date: date) -> pd.DatetimeIndex:
    """Every hour of the local day after `issue_date`: 24 periods, or 23 or 25 around DST."""
    start = _local(issue_date + timedelta(days=1)).tz_convert("UTC")
    end = _local(issue_date + timedelta(days=2)).tz_convert("UTC")
    return pd.date_range(start + HOUR, end, freq="h", name="period_utc")


def local_hour_start(periods: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The local wall-clock time at which each period's hour starts."""
    return (periods - HOUR).tz_convert(config.LOCAL_TZ)


def same_local_hour(periods: pd.DatetimeIndex, days: int) -> pd.DatetimeIndex:
    """The periods covering the same local hours `days` days away.

    An hour skipped by the spring transition maps to the hour after it; either of the two
    hours repeated in autumn maps to the single matching hour on an ordinary day.
    """
    local = local_hour_start(periods)
    in_dst = np.array([stamp.dst() != timedelta(0) for stamp in local])
    shifted = (local.tz_localize(None) + pd.Timedelta(days=days)).tz_localize(
        config.LOCAL_TZ, ambiguous=in_dst, nonexistent="shift_forward"
    )
    return (shifted.tz_convert("UTC") + HOUR).rename(periods.name)
