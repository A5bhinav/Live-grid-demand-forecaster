import { ModelBars } from "@/components/ModelBars";
import { Unconfigured } from "@/components/Unconfigured";
import { getBacktest, type BacktestRow } from "@/lib/data";
import { megawatts, MODEL_NAMES, MODEL_ORDER, percent } from "@/lib/format";

export const revalidate = 3600;

const SEASONS = ["winter", "spring", "summer", "fall"];
const name = (model: string) => MODEL_NAMES[model] ?? model;

function Breakdown({ rows, title, keys }: { rows: BacktestRow[]; title: string; keys: string[] }) {
  const models = MODEL_ORDER.filter((model) => rows.some((row) => row.model === model));
  const cell = (key: string, model: string) => rows.find((row) => row.key === key && row.model === model)?.mape;
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th />
              {models.map((model) => (
                <th key={model}>{name(model)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key}>
                <td>{key[0].toUpperCase() + key.slice(1)}</td>
                {models.map((model) => (
                  <td key={model}>{percent(cell(key, model))}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default async function BacktestPage() {
  const results = await getBacktest();
  if (!results) return <Unconfigured />;
  if (results.length === 0) {
    return (
      <div className="card">
        <p className="empty">No backtest has been saved yet.</p>
      </div>
    );
  }
  const by = (breakdown: BacktestRow["breakdown"]) => results.filter((row) => row.breakdown === breakdown);
  const overall = MODEL_ORDER.flatMap((model) => by("overall").filter((row) => row.model === model));
  const outside = by("time_of_day").filter((row) => row.key === "other hours");
  const months = [...new Set(by("month").map((row) => row.key))].sort();
  const { first_month, last_month, run_at } = results[0];

  return (
    <>
      <h2>Backtest</h2>
      <p className="lede">
        {first_month} to {last_month}, {overall[0].hours.toLocaleString("en-US")} scored hours. The
        model is retrained at the start of each month on everything before it, then each day is
        forecast exactly as the live system would, using only data available that morning.
      </p>
      <div className="card">
        <h3>Error outside 09:00-17:00 Pacific, where the comparison with the operator is fair (MAPE)</h3>
        <ModelBars
          label="MAPE"
          bars={MODEL_ORDER.flatMap((model) =>
            outside.filter((row) => row.model === model).map((row) => ({ model: name(model), mape: row.mape })),
          )}
        />
      </div>
      <div className="card">
        <h3>All hours</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>MAPE</th>
                <th>MAE</th>
                <th>RMSE</th>
                <th>Peak error (mean abs)</th>
              </tr>
            </thead>
            <tbody>
              {overall.map((row) => (
                <tr key={row.model}>
                  <td>{name(row.model)}</td>
                  <td>{percent(row.mape)}</td>
                  <td>{megawatts(row.mae_mw)}</td>
                  <td>{megawatts(row.rmse_mw)}</td>
                  <td>{megawatts(row.peak_abs_error_mw)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Breakdown rows={by("time_of_day")} title="MAPE by time of day" keys={["09:00-17:00", "other hours"]} />
      <Breakdown rows={by("season")} title="MAPE by season" keys={SEASONS} />
      <Breakdown rows={by("month")} title="MAPE by month" keys={months} />
      <p className="note">
        The three LightGBM rows are ablations: calendar and demand lags only, then with temperature
        forecasts, then with solar, cloud, wind and rain. The operator&apos;s all-hours error is
        inflated by a midday mismatch between its forecast and its reported demand, so it is not a
        fair measure of the operator; see the scoreboard for the explanation. Backtests use demand as
        later revised, which a live forecast does not have. Run saved {run_at.slice(0, 10)}.
      </p>
    </>
  );
}
