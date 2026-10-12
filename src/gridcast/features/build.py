"""Builds what a forecast may see at issue time. Backtests and the live job both use this.

This is the only place where data is cut off at the issue-time boundary. Models receive
`IssueInputs` and never touch `Sources` rows directly.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from gridcast import config, protocol
from gridcast.clean.screen import clean


@dataclass(frozen=True)
class Sources:
    """Everything ingested so far. It may extend past any given issue time."""

    raw_demand: pd.Series  # reported demand in MW by hour-ending UTC period


@dataclass(frozen=True)
class IssueInputs:
    issue_date: date
    targets: pd.DatetimeIndex  # the periods to forecast
    demand: pd.Series  # screened demand, hourly, ending exactly at the demand cutoff


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
    return IssueInputs(issue_date, protocol.target_periods(issue_date), demand)
