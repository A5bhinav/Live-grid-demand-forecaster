"""Scoring forecasts against settled actual demand.

Flagged hours are never scored, even when a filled-in value exists. Every forecast is
scored on the same hours: an hour missing from any one of them is skipped for all.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from gridcast import protocol

OPERATOR = "operator"
SEASONS = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}  # fmt: skip
MIDDAY_HOURS = range(9, 17)  # local hours starting 09:00 through 16:00


def score_frame(forecasts: pd.DataFrame, cleaned: pd.DataFrame) -> pd.DataFrame:
    """One row per forecast hour: the actual, why it is skipped if it is, and calendar keys.

    `forecasts` has one column per forecaster. `cleaned` is the output of `clean` on the
    full reported history.
    """
    frame = forecasts.copy()
    actual = cleaned.reindex(frame.index)
    frame["actual_mw"] = actual["demand_mw"].where(actual["flag"].isna())

    skipped = pd.Series(None, index=frame.index, dtype=object)
    for column in reversed(list(forecasts.columns)):
        skipped[forecasts[column].isna()] = f"{column}_missing"
    skipped[frame["actual_mw"].isna()] = "actual_flagged"
    frame["skipped"] = skipped

    local = protocol.local_hour_start(frame.index)
    frame["target_date"] = local.date
    frame["month"] = local.strftime("%Y-%m")
    frame["season"] = local.month.map(SEASONS)
    frame["time_of_day"] = np.where(local.hour.isin(MIDDAY_HOURS), "09:00-17:00", "other hours")
    return frame


def hourly_metrics(
    frame: pd.DataFrame, models: Sequence[str], by: str | None = None
) -> pd.DataFrame:
    """MAPE, MAE and RMSE over scored hours, per model, optionally split by a column."""
    scored = frame[frame["skipped"].isna()]
    groups = [("all", scored)] if by is None else list(scored.groupby(by))
    rows = []
    for key, group in groups:
        for model in models:
            error = group[model] - group["actual_mw"]
            rows.append(
                {
                    by or "scope": key,
                    "model": model,
                    "hours": len(group),
                    "mape": (error.abs() / group["actual_mw"]).mean() * 100,
                    "mae_mw": error.abs().mean(),
                    "rmse_mw": np.sqrt((error**2).mean()),
                }
            )
    return pd.DataFrame(rows)


def daily_metrics(frame: pd.DataFrame, models: Sequence[str]) -> pd.DataFrame:
    """Per target day and model: MAPE, plus peak errors for days with every hour scored."""
    rows = []
    for target_date, day in frame.groupby("target_date"):
        scored = day[day["skipped"].isna()]
        if scored.empty:
            continue
        complete = len(scored) == len(day)
        for model in models:
            error = scored[model] - scored["actual_mw"]
            row = {
                "target_date": target_date,
                "model": model,
                "hours": len(scored),
                "mape": (error.abs() / scored["actual_mw"]).mean() * 100,
                "peak_error_mw": np.nan,
                "peak_hours_off": np.nan,
            }
            if complete:
                row["peak_error_mw"] = scored[model].max() - scored["actual_mw"].max()
                offset = scored[model].idxmax() - scored["actual_mw"].idxmax()
                row["peak_hours_off"] = offset / pd.Timedelta(hours=1)
            rows.append(row)
    return pd.DataFrame(rows)


def day_score(frame: pd.DataFrame, forecast: str) -> dict[str, object]:
    """The `scores` row for one target day, from that day's `score_frame`."""
    models = [forecast, OPERATOR]
    prefixes = {forecast: "", OPERATOR: "operator_"}
    scored = frame[frame["skipped"].isna()]
    other = scored[scored["time_of_day"] == "other hours"]
    row: dict[str, object] = {
        "hours": len(frame),
        "hours_scored": len(scored),
        "other_hours_scored": len(other),
    }
    if scored.empty:
        return row
    hourly = hourly_metrics(frame, models).set_index("model")
    daily = daily_metrics(frame, models).set_index("model")
    for model, prefix in prefixes.items():
        for metric in ("mape", "mae_mw", "rmse_mw"):
            row[prefix + metric] = float(hourly.loc[model, metric])
        for metric in ("peak_error_mw", "peak_hours_off"):
            value = daily.loc[model, metric]
            row[prefix + metric] = None if pd.isna(value) else float(value)
    if not other.empty:
        other_hours = hourly_metrics(other, models).set_index("model")["mape"]
        row["other_hours_mape"] = float(other_hours[forecast])
        row["operator_other_hours_mape"] = float(other_hours[OPERATOR])
    return row


def win_rate(daily: pd.DataFrame, model: str, benchmark: str = OPERATOR) -> float:
    """Percent of days on which `model` has a lower MAPE than `benchmark`."""
    mape = daily.pivot(index="target_date", columns="model", values="mape")
    both = mape[[model, benchmark]].dropna()
    return float((both[model] < both[benchmark]).mean() * 100)
