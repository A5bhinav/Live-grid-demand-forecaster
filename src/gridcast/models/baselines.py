"""Baselines that set the bar for the main model."""

from datetime import date

import pandas as pd

from gridcast import protocol
from gridcast.features.build import IssueInputs, Sources


class SeasonalNaive:
    """Demand at the same local hour seven days earlier."""

    name = "seasonal_naive"
    version = "seasonal_naive-v1"  # recorded on every live forecast row

    def fit(self, sources: Sources, first_issue_date: date) -> None:
        """Nothing to train."""

    def predict(self, inputs: IssueInputs) -> pd.Series:
        last_week = protocol.same_local_hour(inputs.targets, days=-7)
        values = inputs.demand.reindex(last_week).to_numpy()
        return pd.Series(values, index=inputs.targets, name=self.name)
