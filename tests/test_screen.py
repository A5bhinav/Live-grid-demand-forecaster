import numpy as np
import pandas as pd
import pytest

from gridcast.clean.screen import Flag, clean, screen


def synthetic_demand(start: str = "2025-06-01", days: int = 60) -> pd.Series:
    """Realistic clean demand: a local-time daily cycle, lower weekends and small noise."""
    index = pd.date_range(start, periods=days * 24, freq="h", tz="UTC", name="period_utc")
    local = index.tz_convert("America/Los_Angeles")
    daily_cycle = 4000 * np.sin((local.hour - 9) / 24 * 2 * np.pi)
    weekend = np.where(local.dayofweek >= 5, -1500, 0)
    noise = np.random.default_rng(0).normal(0, 150, len(index))
    return pd.Series(25000 + daily_cycle + weekend + noise, index=index, name="demand_mw")


def flagged(flags: pd.Series) -> dict[int, str]:
    """Position -> flag, for the flagged hours only."""
    return {int(i): str(flags.iloc[i]) for i in np.flatnonzero(flags.notna())}


@pytest.fixture
def demand() -> pd.Series:
    return synthetic_demand()


def test_clean_data_is_left_alone(demand: pd.Series) -> None:
    cleaned = clean(demand)

    assert cleaned["flag"].isna().all()
    assert not cleaned["imputed"].any()
    pd.testing.assert_series_equal(cleaned["demand_mw"], demand, check_freq=False)


def test_gaps_are_flagged_missing(demand: pd.Series) -> None:
    demand.iloc[500] = np.nan  # published empty
    demand = demand.drop(demand.index[[700, 701]])  # never published

    flags = screen(demand)

    assert len(flags) == 60 * 24
    assert flagged(flags) == {500: Flag.MISSING, 700: Flag.MISSING, 701: Flag.MISSING}


def test_zeros_and_negatives_are_flagged(demand: pd.Series) -> None:
    demand.iloc[300] = 0.0
    demand.iloc[900] = -1200.0

    assert flagged(screen(demand)) == {300: Flag.NON_POSITIVE, 900: Flag.NON_POSITIVE}


def test_values_far_from_the_overall_median_are_flagged(demand: pd.Series) -> None:
    demand.iloc[400:403] = [14.0, 91.0, 96.0]  # the shape of a real CISO glitch
    demand.iloc[1000] = 400_000.0

    assert flagged(screen(demand)) == {
        400: Flag.GLOBAL_OUTLIER,
        401: Flag.GLOBAL_OUTLIER,
        402: Flag.GLOBAL_OUTLIER,
        1000: Flag.GLOBAL_OUTLIER,
    }


def test_single_hour_spikes_are_flagged(demand: pd.Series) -> None:
    demand.iloc[350] *= 0.55
    demand.iloc[800] += 10_000

    assert flagged(screen(demand)) == {350: Flag.SPIKE, 800: Flag.SPIKE}


def test_a_spike_lasting_several_hours_is_flagged_in_full(demand: pd.Series) -> None:
    demand.iloc[610:613] *= 0.55

    assert flagged(screen(demand)) == dict.fromkeys(range(610, 613), Flag.SPIKE)


def test_a_stuck_value_is_flagged_after_its_first_occurrence(demand: pd.Series) -> None:
    demand.iloc[[200, 201, 202]] = demand.iloc[200]  # consecutive
    demand.iloc[[900, 903, 907, 915]] = demand.iloc[900]  # recurring within a day

    assert flagged(screen(demand)) == dict.fromkeys([201, 202, 903, 907, 915], Flag.REPEATED_VALUE)


def test_a_value_occurring_twice_is_not_treated_as_stuck(demand: pd.Series) -> None:
    demand.iloc[201] = demand.iloc[200]
    demand.iloc[450] = demand.iloc[440]

    assert flagged(screen(demand)) == {}


@pytest.mark.parametrize(
    ("factor", "expected"),
    [(0.5, Flag.LOCAL_OUTLIER_LOW), (2.0, Flag.LOCAL_OUTLIER_HIGH)],
)
def test_a_sustained_level_error_is_flagged_as_a_local_outlier(
    demand: pd.Series, factor: float, expected: Flag
) -> None:
    block = range(700, 712)  # too long to be a spike
    demand.iloc[list(block)] *= factor

    result = flagged(screen(demand))

    assert {position: result[position] for position in block} == dict.fromkeys(block, expected)
    neighbours = set(result) - set(block)
    assert neighbours
    assert {result[position] for position in neighbours} == {Flag.ANOMALOUS_REGION}
    assert min(neighbours) > 700 - 24
    assert max(neighbours) < 712 + 24


def test_a_heat_wave_is_not_flagged(demand: pd.Series) -> None:
    hours_from_peak = np.arange(len(demand)) - 30 * 24
    demand = demand * (1 + 0.6 * np.exp(-((hours_from_peak / 48) ** 2)))
    assert demand.max() > 44_000

    assert flagged(screen(demand)) == {}


@pytest.mark.parametrize("start", ["2026-02-10", "2025-10-05"])
def test_daylight_saving_transitions_are_not_flagged(start: str) -> None:
    demand = synthetic_demand(start=start, days=60)  # spans 2026-03-08 or 2025-11-02

    assert flagged(screen(demand)) == {}


def test_good_hours_among_flagged_ones_are_flagged_but_keep_their_value(
    demand: pd.Series,
) -> None:
    spikes = [600, 603, 606, 609]
    reported = demand.copy()
    reported.iloc[spikes] *= 0.55

    cleaned = clean(reported)
    result = flagged(cleaned["flag"])

    assert {position: result[position] for position in spikes} == dict.fromkeys(spikes, Flag.SPIKE)
    region = cleaned["flag"] == Flag.ANOMALOUS_REGION
    assert {601, 602, 604, 605, 607, 608} <= set(np.flatnonzero(region))
    assert not cleaned.loc[region, "imputed"].any()
    pd.testing.assert_series_equal(
        cleaned.loc[region, "demand_mw"], reported[region], check_freq=False
    )


def test_unflagged_hours_are_never_altered(demand: pd.Series) -> None:
    reported = demand.copy()
    reported.iloc[350] *= 0.55
    reported.iloc[500] = np.nan
    reported.iloc[900] = 0.0

    cleaned = clean(reported)

    untouched = cleaned["flag"].isna()
    assert untouched.sum() == len(reported) - 3
    assert cleaned.loc[~untouched, "imputed"].all()
    pd.testing.assert_series_equal(
        cleaned.loc[untouched, "demand_mw"], reported[untouched], check_freq=False
    )


def test_short_gaps_are_interpolated(demand: pd.Series) -> None:
    reported = demand.copy()
    reported.iloc[[500, 501]] = np.nan

    cleaned = clean(reported)

    before, after = demand.iloc[499], demand.iloc[502]
    assert cleaned["demand_mw"].iloc[500] == pytest.approx(before + (after - before) / 3)
    assert cleaned["demand_mw"].iloc[501] == pytest.approx(before + 2 * (after - before) / 3)
    assert cleaned["imputed"].iloc[[500, 501]].all()


@pytest.mark.parametrize("gap", [slice(700, 712), slice(60 * 24 - 8, None)])
def test_long_and_trailing_gaps_follow_last_weeks_shape(demand: pd.Series, gap: slice) -> None:
    reported = demand.copy()
    reported.iloc[gap] = np.nan

    cleaned = clean(reported)

    assert cleaned["imputed"].iloc[gap].all()
    error = (cleaned["demand_mw"] - demand).iloc[gap].abs()
    assert error.max() < 800  # the daily swing is 8,000 MW


def test_a_gap_with_no_earlier_week_to_copy_stays_missing(demand: pd.Series) -> None:
    reported = demand.copy()
    reported.iloc[30:40] = np.nan

    cleaned = clean(reported)

    assert cleaned["demand_mw"].iloc[30:40].isna().all()
    assert not cleaned["imputed"].iloc[30:40].any()
    assert (cleaned["flag"].iloc[30:40] == Flag.MISSING).all()


def test_row_order_and_timezone_of_the_input_do_not_matter(demand: pd.Series) -> None:
    reported = demand.copy()
    reported.iloc[350] *= 0.55
    shuffled = reported.sample(frac=1, random_state=1).tz_convert("America/Los_Angeles")

    pd.testing.assert_frame_equal(clean(shuffled), clean(reported))


def test_a_naive_index_is_rejected(demand: pd.Series) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        screen(demand.tz_localize(None))


def test_empty_input_is_rejected() -> None:
    empty = pd.Series([], index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    with pytest.raises(ValueError, match="empty"):
        screen(empty)
