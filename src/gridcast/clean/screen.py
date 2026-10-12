"""Screening and imputation for hourly demand.

The screens follow the structure of Ruggles et al. (2020) and PUDL's re-implementation:
basic checks, then repeated values, spikes, deviation from a rolling-median estimate, and
anomalous regions. Each screen ignores hours that an earlier one already flagged.

The thresholds are deliberately loose. The paper's IQR-based local filter flagged about 5%
of CAISO hours, mostly ordinary weekend middays, so the local screen here uses a plain
ratio instead and leaves short glitches to the spike screen. A wrongly flagged hour is
dropped from scoring, which costs more than letting a mildly odd value through.

Several screens look at later hours, so a flag can change as new data arrives. Anything
built for a past issue time must therefore call `clean` on demand truncated at that issue
time's cutoff rather than read a table that was cleaned with hindsight.
"""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

DAY = pd.Timedelta(hours=24)
WEEK_HOURS = 168


class Flag(StrEnum):
    MISSING = "missing"
    NON_POSITIVE = "non_positive"
    GLOBAL_OUTLIER = "global_outlier"
    REPEATED_VALUE = "repeated_value"
    SPIKE = "spike"
    LOCAL_OUTLIER_HIGH = "local_outlier_high"
    LOCAL_OUTLIER_LOW = "local_outlier_low"
    ANOMALOUS_REGION = "anomalous_region"


@dataclass(frozen=True)
class ScreenParams:
    global_median_factor: float = 10.0  # outside median / f .. median * f
    repeat_count: int = 3  # the same value this many times, each within a day of the last
    spike_jump_iqrs: float = 4.0  # jump size, in rolling IQRs of the hourly change
    spike_max_hours: int = 6  # a spike returns to the previous level within this long
    jump_scale_window: int = 240  # hours, rolling IQR of the hourly change
    level_window: int = 48  # hours, rolling median that tracks the demand level
    pattern_days: int = 10  # days either side used for the hour-of-day pattern
    local_factor: float = 1.5  # outside estimate / f .. estimate * f
    region_window: int = 24  # hours
    region_flagged_share: float = 0.15
    max_linear_gap: int = 3  # hours; longer gaps use last week's shape


DEFAULT_PARAMS = ScreenParams()


def _hourly(demand: pd.Series) -> pd.Series:
    if demand.empty:
        raise ValueError("demand is empty")
    if demand.index.tz is None:
        raise ValueError("demand must have a timezone-aware index")
    index = demand.index.tz_convert("UTC")
    full = pd.date_range(index.min(), index.max(), freq="h", name=demand.index.name)
    return pd.Series(demand.to_numpy(dtype=float), index=index).reindex(full)


def _repeated(valid: pd.Series, params: ScreenParams) -> pd.Series:
    """A stuck feed: every occurrence after the first of a value that keeps recurring."""
    seen = valid.dropna().rename("value").rename_axis("time").reset_index()
    seen = seen.sort_values(["value", "time"])
    gap = seen.groupby("value")["time"].diff()
    cluster = (gap.isna() | (gap > DAY)).cumsum()
    size = seen.groupby(cluster)["time"].transform("size")
    position = seen.groupby(cluster).cumcount()
    stuck = seen.loc[(size >= params.repeat_count) & (position >= 1), "time"]
    return pd.Series(valid.index.isin(stuck), index=valid.index)


def _spikes(valid: pd.Series, params: ScreenParams) -> pd.Series:
    """Hours between a large jump and an opposite large jump shortly afterwards."""
    change = valid.diff()
    window = params.jump_scale_window
    rolling = change.rolling(window, center=True, min_periods=window // 2)
    scale = rolling.quantile(0.75) - rolling.quantile(0.25)
    size = (change / scale).to_numpy()
    jumps = np.flatnonzero(np.abs(size) > params.spike_jump_iqrs)

    mask = np.zeros(len(valid), dtype=bool)
    i = 0
    while i < len(jumps) - 1:
        start, end = jumps[i], jumps[i + 1]
        returns = np.sign(size[start]) != np.sign(size[end])
        if returns and end - start <= params.spike_max_hours:
            mask[start:end] = True
            i += 2
        else:
            i += 1
    return pd.Series(mask, index=valid.index)


def _local_estimate(valid: pd.Series, params: ScreenParams) -> pd.Series:
    """The local demand level times the typical hour-of-day shape of nearby days."""
    window = params.level_window
    level = valid.rolling(window, center=True, min_periods=window // 2).median()
    relative = valid / level
    days = range(-params.pattern_days, params.pattern_days + 1)
    pattern = pd.concat([relative.shift(24 * day) for day in days], axis=1).median(axis=1)
    return level * pattern


def screen(demand: pd.Series, params: ScreenParams = DEFAULT_PARAMS) -> pd.Series:
    """Flag suspect hours. Returns a `Flag` (or None) for every hour from first to last."""
    values = _hourly(demand)
    flags = pd.Series(None, index=values.index, dtype=object, name="flag")

    def apply(mask: pd.Series, flag: Flag) -> pd.Series:
        flags[mask.to_numpy() & flags.isna().to_numpy()] = flag
        return values.where(flags.isna())

    valid = apply(values.isna(), Flag.MISSING)
    valid = apply(values <= 0, Flag.NON_POSITIVE)

    median = valid.median()
    factor = params.global_median_factor
    valid = apply((valid > median * factor) | (valid < median / factor), Flag.GLOBAL_OUTLIER)

    valid = apply(_repeated(valid, params), Flag.REPEATED_VALUE)
    valid = apply(_spikes(valid, params), Flag.SPIKE)

    estimate = _local_estimate(valid, params)
    valid = apply(valid > estimate * params.local_factor, Flag.LOCAL_OUTLIER_HIGH)
    valid = apply(valid < estimate / params.local_factor, Flag.LOCAL_OUTLIER_LOW)

    # Hours that look fine but sit among flagged ones are not trusted either.
    flagged = flags.notna().astype(float)
    share = flagged.rolling(params.region_window, center=True, min_periods=1).mean()
    apply(share > params.region_flagged_share, Flag.ANOMALOUS_REGION)
    return flags


def _keeps_value(flags: pd.Series) -> pd.Series:
    return flags.isna() | (flags == Flag.ANOMALOUS_REGION)


def impute(demand: pd.Series, flags: pd.Series, params: ScreenParams = DEFAULT_PARAMS) -> pd.Series:
    """Fill flagged hours. Hours that cannot be filled stay missing.

    Gaps of up to `max_linear_gap` hours between good values are interpolated. Longer gaps,
    and gaps at either end, take the shape of the same hours one week earlier, shifted to
    meet the good values at the gap's edges. Hours flagged only as part of an anomalous
    region keep their reported value, which is plausible on its own.
    """
    values = _hourly(demand)
    valid = values.where(_keeps_value(flags))
    is_gap = valid.isna()
    gap_id = is_gap.ne(is_gap.shift()).cumsum()[is_gap]
    gap_length = gap_id.map(gap_id.value_counts())

    interpolated = valid.interpolate(method="linear", limit_area="inside")
    short = gap_length[gap_length <= params.max_linear_gap].index
    filled = valid.copy()
    filled[short] = interpolated[short]

    # Whatever is left takes last week's values, shifted by the offset between the two
    # weeks at the nearest good hours on either side.
    last_week = valid.shift(WEEK_HOURS)
    offset = (valid - last_week).interpolate(method="linear", limit_direction="both")
    remaining = filled.isna()
    filled[remaining] = (last_week + offset)[remaining]
    return filled.rename("demand_mw")


def clean(demand: pd.Series, params: ScreenParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Screen and impute. Returns `demand_mw`, `flag` and `imputed` for every hour."""
    flags = screen(demand, params)
    filled = impute(demand, flags, params)
    imputed = ~_keeps_value(flags) & filled.notna()
    return pd.DataFrame({"demand_mw": filled, "flag": flags, "imputed": imputed})
