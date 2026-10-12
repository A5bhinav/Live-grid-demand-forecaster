"""Builds what a forecast may see at issue time. Backtests and the live job both use this.

`build_inputs` is the only place where data is cut off at the issue-time boundary, and
`feature_frame` is the only place where features are computed. Models receive
`IssueInputs` and never touch `Sources` rows directly.

Alignment: a period is an hour-ending UTC timestamp and is matched to the weather forecast
valid at that same timestamp. Radiation and precipitation cover the preceding hour, so
they line up exactly; temperature is the reading at the end of the hour.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property

import holidays
import numpy as np
import pandas as pd

from gridcast import config, protocol
from gridcast.clean.screen import clean

WEATHER_COLUMNS = ("location", "valid_time_utc", "lead_days", "model", *config.WEATHER_VARIABLES)
LOCATION_NAMES = tuple(location.name for location in config.LOCATIONS)

CALENDAR_FEATURES = (
    "hour", "day_of_week", "month", "day_of_year", "is_weekend", "is_dst",
    "is_holiday", "holiday_tomorrow", "holiday_yesterday",
)  # fmt: skip
LAG_FEATURES = (
    "demand_lag_2d", "demand_lag_3d", "demand_lag_7d", "demand_lag_8d", "demand_lag_14d",
    "demand_last_day_mean", "demand_last_day_max", "demand_last_day_min",
    "demand_week_ago_day_mean", "demand_7d_mean",
)  # fmt: skip
TEMPERATURE_FEATURES = (
    *(f"temp_{name}" for name in LOCATION_NAMES),
    "temp_mean", "temp_min", "temp_max", "cooling_degree_hours", "heating_degree_hours",
    "temp_day_max", "temp_day_min", "temp_day_mean", "temp_change_2d", "temp_change_7d",
    "dew_point_mean", "humidity_mean",
)  # fmt: skip
SOLAR_FEATURES = (
    "radiation_mean", "radiation_day_mean", "cloud_cover_mean",
    "wind_speed_mean", "precipitation_mean",
)  # fmt: skip

FEATURE_SETS = {
    "calendar_lags": (*CALENDAR_FEATURES, *LAG_FEATURES),
    "plus_temperature": (*CALENDAR_FEATURES, *LAG_FEATURES, *TEMPERATURE_FEATURES),
    "full": (*CALENDAR_FEATURES, *LAG_FEATURES, *TEMPERATURE_FEATURES, *SOLAR_FEATURES),
}


def _no_weather() -> pd.DataFrame:
    return pd.DataFrame(columns=list(WEATHER_COLUMNS))


@dataclass(frozen=True, eq=False)
class Sources:
    """Everything ingested so far. It may extend past any given issue time."""

    raw_demand: pd.Series  # reported demand in MW by hour-ending UTC period
    weather: pd.DataFrame = field(default_factory=_no_weather)  # rows of `raw_weather`
    _features: dict[date, pd.DataFrame] = field(default_factory=dict, repr=False)

    @cached_property
    def forecast_weather(self) -> pd.DataFrame:
        """Day-2 forecasts from the pinned model: valid time by (variable, location)."""
        usable = self.weather[
            (self.weather["lead_days"] == config.WEATHER_LEAD_DAYS)
            & (self.weather["model"] == config.WEATHER_MODEL)
        ]
        wide = usable.pivot(
            index="valid_time_utc", columns="location", values=list(config.WEATHER_VARIABLES)
        )
        columns = pd.MultiIndex.from_product([config.WEATHER_VARIABLES, LOCATION_NAMES])
        return wide.reindex(columns=columns).astype(float)


@dataclass(frozen=True)
class IssueInputs:
    issue_date: date
    targets: pd.DatetimeIndex  # the periods to forecast
    demand: pd.Series  # screened demand, hourly, ending exactly at the demand cutoff
    weather: pd.DataFrame  # day-2 forecasts valid up to the last target, no later


def build_inputs(issue_date: date, sources: Sources) -> IssueInputs:
    cutoff = protocol.demand_cutoff(issue_date)
    start = cutoff - pd.Timedelta(days=config.FEATURE_LOOKBACK_DAYS)
    hours = pd.date_range(start, cutoff, freq="h", name="period_utc")
    # Demand is screened using only what was available, so flags and filled-in values
    # cannot depend on anything after the cutoff. Hours not yet published are filled in.
    available = sources.raw_demand.reindex(hours)
    if available.isna().all():
        raise ValueError(f"no demand available for a forecast issued on {issue_date}")
    demand = clean(available)["demand_mw"]

    targets = protocol.target_periods(issue_date)
    # A day-2 forecast for the last target hour was made before the issue time, and
    # everything earlier is older still.
    weather = sources.forecast_weather.loc[start : targets[-1]]
    return IssueInputs(issue_date, targets, demand, weather)


def _holiday_flags(days: pd.Index) -> np.ndarray:
    calendar = holidays.country_holidays("US", subdiv="CA")
    return np.array([day in calendar for day in days], dtype=float)


def feature_frame(inputs: IssueInputs) -> pd.DataFrame:
    """One row per target period with every feature a model may use."""
    targets = inputs.targets
    local = protocol.local_hour_start(targets)
    features = pd.DataFrame(index=targets)

    features["hour"] = local.hour
    features["day_of_week"] = local.dayofweek
    features["month"] = local.month
    features["day_of_year"] = local.dayofyear
    features["is_weekend"] = (local.dayofweek >= 5).astype(float)
    features["is_dst"] = np.array([stamp.dst() != timedelta(0) for stamp in local], dtype=float)
    target_days = pd.Index(local.date)
    features["is_holiday"] = _holiday_flags(target_days)
    features["holiday_tomorrow"] = _holiday_flags(target_days + timedelta(days=1))
    features["holiday_yesterday"] = _holiday_flags(target_days - timedelta(days=1))

    # The target day is two days after the last full day of demand, so the nearest
    # same-hour lag is two days.
    demand = inputs.demand
    for days in (2, 3, 7, 8, 14):
        source = protocol.same_local_hour(targets, -days)
        features[f"demand_lag_{days}d"] = demand.reindex(source).to_numpy()
    last_day = demand.reindex(protocol.target_periods(inputs.issue_date - timedelta(days=2)))
    week_ago_day = demand.reindex(protocol.target_periods(inputs.issue_date - timedelta(days=7)))
    features["demand_last_day_mean"] = last_day.mean()
    features["demand_last_day_max"] = last_day.max()
    features["demand_last_day_min"] = last_day.min()
    features["demand_week_ago_day_mean"] = week_ago_day.mean()
    features["demand_7d_mean"] = demand.iloc[-7 * 24 :].mean()

    weather = inputs.weather
    at_targets = weather.reindex(targets)
    temperature = at_targets["temperature_2m"]
    for name in LOCATION_NAMES:
        features[f"temp_{name}"] = temperature[name]
    temp_mean = temperature.mean(axis=1)
    base = config.DEGREE_HOUR_BASE_C
    features["temp_mean"] = temp_mean
    features["temp_min"] = temperature.min(axis=1)
    features["temp_max"] = temperature.max(axis=1)
    features["cooling_degree_hours"] = (temperature - base).clip(lower=0).mean(axis=1)
    features["heating_degree_hours"] = (base - temperature).clip(lower=0).mean(axis=1)
    features["temp_day_max"] = temp_mean.max()
    features["temp_day_min"] = temp_mean.min()
    features["temp_day_mean"] = temp_mean.mean()
    for days in (2, 7):
        earlier = weather["temperature_2m"].reindex(protocol.same_local_hour(targets, -days))
        features[f"temp_change_{days}d"] = temp_mean.to_numpy() - earlier.mean(axis=1).to_numpy()
    features["dew_point_mean"] = at_targets["dew_point_2m"].mean(axis=1)
    features["humidity_mean"] = at_targets["relative_humidity_2m"].mean(axis=1)

    radiation = at_targets["shortwave_radiation"].mean(axis=1)
    features["radiation_mean"] = radiation
    features["radiation_day_mean"] = radiation.mean()
    features["cloud_cover_mean"] = at_targets["cloud_cover"].mean(axis=1)
    features["wind_speed_mean"] = at_targets["wind_speed_10m"].mean(axis=1)
    features["precipitation_mean"] = at_targets["precipitation"].mean(axis=1)
    return features.astype(float)


def cached_features(issue_date: date, sources: Sources) -> pd.DataFrame:
    """`feature_frame` for an issue date, computed once per `Sources`."""
    if issue_date not in sources._features:
        sources._features[issue_date] = feature_frame(build_inputs(issue_date, sources))
    return sources._features[issue_date]
