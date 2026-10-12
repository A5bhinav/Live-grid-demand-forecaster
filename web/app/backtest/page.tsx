import type { Metadata } from "next";
import { ModelBars } from "@/components/ModelBars";
import { Unconfigured } from "@/components/Unconfigured";
import { Card, Empty, PageHeader, Stat } from "@/components/ui";
import { getBacktest, type BacktestRow } from "@/lib/data";
import { megawatts, MODEL_NAMES, MODEL_ORDER, percent } from "@/lib/format";

export const revalidate = 3600;
export const metadata: Metadata = { title: "Backtest" };

const LIVE_MODEL = "gbm_full";
const SEASONS = ["winter", "spring", "summer", "fall"];
const name = (model: string) => MODEL_NAMES[model] ?? model;
const sentence = (text: string) => text[0].toUpperCase() + text.slice(1);

function Breakdown({ rows, title, keys }: { rows: BacktestRow[]; title: string; keys: string[] }) {
  const models = MODEL_ORDER.filter((model) => rows.some((row) => row.model === model));
  const cell = (key: string, model: string) => rows.find((row) => row.key === key && row.model === model)?.mape;
  return (
    <Card title={title} flush>
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
                <td>{sentence(key)}</td>
                {models.map((model) => (
                  <td key={model} className={model === LIVE_MODEL ? "emphasis" : undefined}>
                    {percent(cell(key, model))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default async function BacktestPage() {
  const results = await getBacktest();
  if (!results) return <Unconfigured />;
  const header = (
    <PageHeader eyebrow="Method check" title="Backtest">
      Twelve months replayed day by day. The model is retrained at the start of each month on
      everything before it, then each day is forecast exactly as the live system would, using only
      data available that morning.
    </PageHeader>
  );
  if (results.length === 0) {
    return (
      <>
        {header}
        <section className="card">
          <Empty title="No backtest saved yet">Run the backtest job with --save to publish one here.</Empty>
        </section>
      </>
    );
  }

  const by = (breakdown: BacktestRow["breakdown"]) => results.filter((row) => row.breakdown === breakdown);
  const overall = MODEL_ORDER.flatMap((model) => by("overall").filter((row) => row.model === model));
  const outside = by("time_of_day").filter((row) => row.key === "other hours");
  const find = (rows: BacktestRow[], model: string) => rows.find((row) => row.model === model);
  const months = [...new Set(by("month").map((row) => row.key))].sort();
  const { first_month, last_month, run_at } = results[0];

  const ours = find(overall, LIVE_MODEL);
  const naive = find(overall, "seasonal_naive");
  const oursOutside = find(outside, LIVE_MODEL);
  const operatorOutside = find(outside, "operator");
  const cut = ours && naive ? (1 - ours.mape / naive.mape) * 100 : null;

  return (
    <>
      {header}
      <div className="stats">
        <Stat label="Error, all hours" value={percent(ours?.mape)} note="LightGBM, full feature set" />
        <Stat
          label="Against the baseline"
          value={cut == null ? "n/a" : `${cut.toFixed(0)}% lower`}
          note={`seasonal naive ${percent(naive?.mape)}`}
        />
        <Stat
          label="Outside 09:00-17:00"
          value={percent(oursOutside?.mape)}
          note={`operator ${percent(operatorOutside?.mape)}`}
        />
        <Stat
          label="Period"
          value={`${first_month} to ${last_month}`}
          text
          note={`${overall[0].hours.toLocaleString("en-US")} scored hours`}
        />
      </div>
      <Card
        title="Error outside 09:00-17:00 Pacific"
        subtitle="Mean absolute percentage error in the hours where the comparison with the operator is fair. Lower is better."
      >
        <ModelBars
          bars={MODEL_ORDER.flatMap((model) =>
            outside
              .filter((row) => row.model === model)
              .map((row) => ({
                model: name(model),
                mape: row.mape,
                role: model === LIVE_MODEL ? ("ours" as const) : model === "operator" ? ("operator" as const) : ("other" as const),
              })),
          )}
        />
      </Card>
      <Card title="All hours" flush>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>MAPE</th>
                <th>MAE</th>
                <th>RMSE</th>
                <th>Daily peak error</th>
              </tr>
            </thead>
            <tbody>
              {overall.map((row) => (
                <tr key={row.model} className={row.model === LIVE_MODEL ? "emphasis" : undefined}>
                  <td>
                    {name(row.model)}
                    {row.model === LIVE_MODEL ? (
                      <span className="badge accent" style={{ marginLeft: 10 }}>
                        Live model
                      </span>
                    ) : null}
                  </td>
                  <td>{percent(row.mape)}</td>
                  <td>{megawatts(row.mae_mw)}</td>
                  <td>{megawatts(row.rmse_mw)}</td>
                  <td>{megawatts(row.peak_abs_error_mw)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Breakdown rows={by("time_of_day")} title="Error by time of day" keys={["09:00-17:00", "other hours"]} />
      <Breakdown rows={by("season")} title="Error by season" keys={SEASONS} />
      <Breakdown rows={by("month")} title="Error by month" keys={months} />
      <p className="callout">
        <strong>How to read this.</strong> The three LightGBM rows are ablations: calendar and demand
        lags only, then with temperature forecasts, then with solar, cloud, wind and rain. The
        operator&apos;s all-hours error is inflated by a midday mismatch between its forecast and its
        reported demand, so it is not a fair measure of the operator; the scoreboard explains why.
        Backtests use demand as later revised, which a live forecast does not have. Run saved{" "}
        {run_at.slice(0, 10)}.
      </p>
    </>
  );
}
