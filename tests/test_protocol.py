from datetime import date

import pandas as pd
import pytest

from gridcast import protocol


def utc(stamp: str) -> pd.Timestamp:
    return pd.Timestamp(stamp, tz="UTC")


def test_an_ordinary_target_day_has_24_hours_ending_at_local_midnight() -> None:
    targets = protocol.target_periods(date(2026, 7, 14))  # PDT, UTC-7

    assert len(targets) == 24
    assert targets[0] == utc("2026-07-15 08:00")  # hour ending 01:00 local
    assert targets[-1] == utc("2026-07-16 07:00")  # hour ending 24:00 local


@pytest.mark.parametrize(
    ("issue_date", "hours"),
    [(date(2026, 3, 7), 23), (date(2025, 11, 1), 25), (date(2026, 10, 31), 25)],
)
def test_daylight_saving_target_days_have_23_or_25_hours(issue_date: date, hours: int) -> None:
    targets = protocol.target_periods(issue_date)

    assert len(targets) == hours
    assert targets.is_unique
    local_days = set(protocol.local_hour_start(targets).date)
    assert local_days == {issue_date + pd.Timedelta(days=1)}


def test_consecutive_target_days_tile_time_without_gaps_or_overlap() -> None:
    days = pd.date_range("2025-10-25", "2026-03-15")
    periods = pd.DatetimeIndex(
        [period for day in days for period in protocol.target_periods(day.date())]
    )

    assert periods.is_unique
    assert (periods[1:] - periods[:-1] == pd.Timedelta(hours=1)).all()


@pytest.mark.parametrize("issue_date", [date(2026, 1, 15), date(2026, 7, 15), date(2026, 3, 8)])
def test_demand_cutoff_precedes_issue_time_which_precedes_the_targets(issue_date: date) -> None:
    cutoff = protocol.demand_cutoff(issue_date)
    issued = protocol.issue_time(issue_date)

    assert cutoff < issued < protocol.target_periods(issue_date)[0]
    assert cutoff.tz_convert("America/Los_Angeles").strftime("%Y-%m-%d %H:%M") == (
        f"{issue_date} 00:00"
    )
    assert issued.tz_convert("America/Los_Angeles").hour == 9


def test_same_local_hour_is_168_hours_back_when_no_transition_intervenes() -> None:
    targets = protocol.target_periods(date(2026, 7, 14))

    shifted = protocol.same_local_hour(targets, days=-7)

    assert (targets - shifted == pd.Timedelta(hours=168)).all()


def test_same_local_hour_keeps_the_wall_clock_across_a_transition() -> None:
    targets = protocol.target_periods(date(2026, 3, 9))  # the Tuesday after spring forward

    shifted = protocol.same_local_hour(targets, days=-7)

    assert (targets - shifted == pd.Timedelta(hours=167)).all()
    assert list(protocol.local_hour_start(shifted).hour) == list(range(24))


def test_the_hour_skipped_in_spring_maps_to_the_hour_after_it() -> None:
    targets = protocol.target_periods(date(2026, 3, 14))  # a week after the 23-hour day

    source_hours = list(protocol.local_hour_start(protocol.same_local_hour(targets, -7)).hour)

    assert source_hours == [0, 1, 3, 3, *range(4, 24)]


def test_both_repeated_autumn_hours_map_to_the_one_hour_a_week_earlier() -> None:
    targets = protocol.target_periods(date(2025, 11, 1))  # the 25-hour day

    source_hours = list(protocol.local_hour_start(protocol.same_local_hour(targets, -7)).hour)

    assert source_hours == [0, 1, 1, *range(2, 24)]
