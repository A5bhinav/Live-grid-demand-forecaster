import { DailyMapeChart } from "@/components/DailyMapeChart";
import { Tile } from "@/components/Tile";
import { Unconfigured } from "@/components/Unconfigured";
import { getScores, getStatus, type ScoreRow } from "@/lib/data";
import { dayLabel, megawatts, percent } from "@/lib/format";

export const revalidate = 300;

type Key = "mape" | "operator_mape" | "other_hours_mape" | "operator_other_hours_mape";

function weighted(rows: ScoreRow[], key: Key): number | null {
  const weight = key.includes("other") ? "other_hours_scored" : "hours_scored";
  const used = rows.filter((row) => row[key] != null && row[weight] > 0);
  const hours = used.reduce((sum, row) => sum + row[weight], 0);
  return hours ? used.reduce((sum, row) => sum + (row[key] as number) * row[weight], 0) / hours : null;
}

function winRate(rows: ScoreRow[], ours: Key, theirs: Key): number | null {
  const both = rows.filter((row) => row[ours] != null && row[theirs] != null);
  return both.length ? (both.filter((row) => row[ours]! < row[theirs]!).length / both.length) * 100 : null;
}

export default async function ScoreboardPage() {
  const [scores, status] = await Promise.all([getScores(), getStatus()]);
  if (!scores || !status) return <Unconfigured />;
  const scored = scores.filter((row) => row.hours_scored > 0);
  const windows = [
    { label: "Last 7 scored days", rows: scored.slice(-7) },
    { label: "Last 30 scored days", rows: scored.slice(-30) },
    { label: "All scored days", rows: scored },
  ];

  return (
    <>
      <h2>Scoreboard</h2>
      <p className="lede">
        Each forecast day is scored once, 72 hours after it ends, against reported demand and against
        the operator&apos;s own day-ahead forecast on the same hours.
      </p>
      <div className="tiles">
        <Tile label="Days forecast" value={String(status.days_forecast)} />
        <Tile label="Days scored" value={String(status.days_scored)} />
        {scored.length > 0 ? (
          <>
            <Tile
              label="7-day MAPE, outside 09:00-17:00"
              value={percent(weighted(windows[0].rows, "other_hours_mape"))}
              note={`operator ${percent(weighted(windows[0].rows, "operator_other_hours_mape"))}`}
            />
            <Tile label="7-day MAPE, all hours" value={percent(weighted(windows[0].rows, "mape"))} />
          </>
        ) : null}
      </div>
      {scored.length === 0 ? (
        <div className="card">
          <p className="empty">
            No day has been scored yet. A day is scored 72 hours after it ends, so the first score
            appears four days after the first forecast day.
          </p>
        </div>
      ) : (
        <>
          <div className="card">
            <h3>Mean absolute percentage error</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Window</th>
                    <th>Days</th>
                    <th>Ours, outside 09:00-17:00</th>
                    <th>Operator, outside 09:00-17:00</th>
                    <th>Days we were closer</th>
                    <th>Ours, all hours</th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map((window) => (
                    <tr key={window.label}>
                      <td>{window.label}</td>
                      <td>{window.rows.length}</td>
                      <td>{percent(weighted(window.rows, "other_hours_mape"))}</td>
                      <td>{percent(weighted(window.rows, "operator_other_hours_mape"))}</td>
                      <td>{percent(winRate(window.rows, "other_hours_mape", "operator_other_hours_mape"), 0)}</td>
                      <td>{percent(weighted(window.rows, "mape"))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <h3>Daily error</h3>
            <DailyMapeChart
              points={scored.map((row) => ({
                day: row.target_date.slice(5),
                ours: row.mape,
                operator: row.operator_mape,
                oursOther: row.other_hours_mape,
                operatorOther: row.operator_other_hours_mape,
              }))}
            />
          </div>
          <div className="card">
            <h3>Scored days</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Model</th>
                    <th>Hours scored</th>
                    <th>Ours, outside midday</th>
                    <th>Operator, outside midday</th>
                    <th>Ours, all hours</th>
                    <th>Peak error</th>
                  </tr>
                </thead>
                <tbody>
                  {[...scores].reverse().map((row) => (
                    <tr key={row.model_version + row.target_date}>
                      <td>{dayLabel(row.target_date)}</td>
                      <td>{row.model_version}</td>
                      <td>
                        {row.hours_scored} of {row.hours}
                      </td>
                      <td>{percent(row.other_hours_mape)}</td>
                      <td>{percent(row.operator_other_hours_mape)}</td>
                      <td>{percent(row.mape)}</td>
                      <td>{megawatts(row.peak_error_mw)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      <p className="note">
        Why the comparison uses hours outside 09:00-17:00: since 2023 the operator&apos;s published
        forecast and its reported demand have diverged in the middle of the day, by about 7 GW in
        2026, so the two no longer measure the same quantity in those hours. Outside them they agree,
        and that is where the head-to-head is fair. Our all-hours error against reported demand is
        shown separately. A day on which the operator published no forecast is not scored.
      </p>
    </>
  );
}
