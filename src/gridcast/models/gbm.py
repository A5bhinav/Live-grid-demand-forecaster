"""The main model: one LightGBM model across all target hours."""

import json
from dataclasses import dataclass
from datetime import date, timedelta

import lightgbm as lgb
import pandas as pd

from gridcast import config, protocol
from gridcast.clean.screen import clean
from gridcast.features.build import (
    FEATURE_SETS,
    IssueInputs,
    Sources,
    cached_features,
    feature_frame,
)

DEFAULT_PARAMS = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 0,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbose": -1,
}
MAX_ROUNDS = 3000
EARLY_STOPPING_ROUNDS = 100


@dataclass(frozen=True)
class TrainingSet:
    features: pd.DataFrame
    target: pd.Series  # actual demand in MW


def training_set(sources: Sources, first_issue_date: date) -> TrainingSet:
    """Rows for every target day whose demand was known when `first_issue_date` arrived."""
    cutoff = protocol.demand_cutoff(first_issue_date)
    # Labels are screened as of the same cutoff, and flagged hours are not learned from.
    labels = clean(sources.raw_demand[sources.raw_demand.index <= cutoff])
    actual = labels["demand_mw"].where(labels["flag"].isna())

    last_issue_date = first_issue_date - timedelta(days=2)  # its target day ends at the cutoff
    issue_dates = pd.date_range(config.TRAIN_START, last_issue_date)
    features = pd.concat([cached_features(stamp.date(), sources) for stamp in issue_dates])
    target = actual.reindex(features.index)
    known = target.notna()
    return TrainingSet(features[known], target[known])


class GbmForecaster:
    def __init__(
        self,
        feature_set: str = "full",
        *,
        name: str | None = None,
        version: str | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        self.feature_set = feature_set
        self.columns = list(FEATURE_SETS[feature_set])
        self.name = name or f"gbm_{feature_set}"
        self.version = version or self.name
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.booster: lgb.Booster | None = None
        self.rounds: int | None = None
        self.train_end: pd.Timestamp | None = None

    def fit(self, sources: Sources, first_issue_date: date) -> None:
        data = training_set(sources, first_issue_date)
        features, target = data.features[self.columns], data.target
        self.train_end = features.index.max()

        # Pick the number of rounds on the tail of the window, then refit on all of it.
        tail = features.index > self.train_end - pd.Timedelta(days=config.EARLY_STOPPING_DAYS)
        fitted = lgb.train(
            self.params,
            lgb.Dataset(features[~tail], target[~tail]),
            num_boost_round=MAX_ROUNDS,
            valid_sets=[lgb.Dataset(features[tail], target[tail])],
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        self.rounds = fitted.best_iteration
        self.booster = lgb.train(
            self.params, lgb.Dataset(features, target), num_boost_round=self.rounds
        )

    def predict(self, inputs: IssueInputs) -> pd.Series:
        if self.booster is None:
            raise RuntimeError(f"{self.name} has not been fitted")
        features = feature_frame(inputs)[self.columns]
        return pd.Series(self.booster.predict(features), index=inputs.targets, name=self.name)

    def to_artifact(self) -> bytes:
        if self.booster is None:
            raise RuntimeError(f"{self.name} has not been fitted")
        document = {
            "kind": "gbm",
            "feature_set": self.feature_set,
            "columns": self.columns,
            "params": self.params,
            "rounds": self.rounds,
            "train_end": self.train_end.isoformat(),
            "model": self.booster.model_to_string(),
        }
        return json.dumps(document).encode()

    @classmethod
    def from_artifact(cls, artifact: bytes, version: str) -> "GbmForecaster":
        document = json.loads(artifact)
        forecaster = cls(document["feature_set"], version=version, params=document["params"])
        forecaster.columns = document["columns"]
        forecaster.rounds = document["rounds"]
        forecaster.train_end = pd.Timestamp(document["train_end"])
        forecaster.booster = lgb.Booster(model_str=document["model"])
        return forecaster
