"""Backtest job: score the baselines and the operator's forecast over recent months.

uv run python -m gridcast.jobs.backtest [--months 12]
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from gridcast import config, db, scoring
from gridcast.backtest.rolling import Forecaster, run_backtest, settled_months
from gridcast.clean.screen import clean
from gridcast.features.build import Sources
from gridcast.models.baselines import SeasonalNaive


@dataclass(frozen=True)
class BacktestReport:
    frame: pd.DataFrame  # one row per target hour
    models: list[str]  # the operator's forecast last
    daily: pd.DataFrame

    def overall(self) -> pd.DataFrame:
        table = scoring.hourly_metrics(self.frame, self.models).set_index("model")
        peaks = self.daily.assign(
            peak_error_mw=self.daily["peak_error_mw"].abs(),
            peak_hours_off=self.daily["peak_hours_off"].abs(),
        ).groupby("model")[["peak_error_mw", "peak_hours_off"]]
        table["peak_abs_error_mw"] = peaks.mean()["peak_error_mw"]
        table["peak_abs_hours_off"] = peaks.mean()["peak_hours_off"]
        table["win_rate_vs_operator"] = [
            float("nan") if model == scoring.OPERATOR else scoring.win_rate(self.daily, model)
            for model in table.index
        ]
        return table.drop(columns="scope")

    def mape_by(self, column: str) -> pd.DataFrame:
        table = scoring.hourly_metrics(self.frame, self.models, by=column)
        return table.pivot(index=column, columns="model", values="mape")[self.models]

    def skipped(self) -> pd.Series:
        return self.frame["skipped"].fillna("scored").value_counts()


def backtest_report(
    forecasters: Sequence[Forecaster],
    raw_demand: pd.Series,
    operator: pd.Series,
    months: Sequence[pd.Period],
) -> BacktestReport:
    forecasts = run_backtest(forecasters, Sources(raw_demand=raw_demand), months)
    # The operator's forecast is the opponent: scored as published, never an input.
    forecasts[scoring.OPERATOR] = operator.reindex(forecasts.index)
    models = list(forecasts.columns)
    frame = scoring.score_frame(forecasts, clean(raw_demand))
    return BacktestReport(frame, models, scoring.daily_metrics(frame, models))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", type=int, default=12)
    args = parser.parse_args()

    with db.connect(config.Settings().require_database_url()) as conn:
        raw_demand = db.read_raw_demand(conn)
        operator = db.read_raw_eia(conn, config.EIA_TYPE_OPERATOR_FORECAST)

    months = settled_months(raw_demand.index.max(), args.months)
    report = backtest_report([SeasonalNaive()], raw_demand, operator, months)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda value: f"{value:,.2f}")
    print(f"Target months {months[0]} to {months[-1]}, {len(report.frame):,} target hours\n")
    print(report.skipped().to_string(), "\n")
    print(report.overall().to_string(), "\n")
    for column in ("time_of_day", "season", "month"):
        print(f"MAPE (%) by {column}")
        print(report.mape_by(column).to_string(), "\n")
    print("Win rate vs operator (%) by month")
    daily = report.daily.assign(
        month=pd.to_datetime(report.daily["target_date"]).dt.strftime("%Y-%m")
    )
    wins = {
        month: scoring.win_rate(days, SeasonalNaive.name) for month, days in daily.groupby("month")
    }
    print(pd.Series(wins, name=SeasonalNaive.name).to_string())


if __name__ == "__main__":
    main()
