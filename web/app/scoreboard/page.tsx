import type { Metadata } from "next";
import { DailyMapeChart } from "@/components/DailyMapeChart";
import { Unconfigured } from "@/components/Unconfigured";
import { Card, Empty, PageHeader, Stat } from "@/components/ui";
import { getHourly, getScores, getStatus, type ScoreRow } from "@/lib/data";
import { addDays, dayLabel, hourStart, localDay, megawatts, modelLabel, percent } from "@/lib/format";

export const revalidate = 300;
export const metadata: Metadata = { title: "Scoreboard" };

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
  const [scores, status, hourly] = await Promise.all([getScores(), getStatus(), getHourly()]);
  if (!scores || !status || !hourly) return <Unconfigured />;
  const scored = scores.filter((row) => row.hours_scored > 0);
  const windows = [
    { label: "Last 7 scored days", rows: scored.slice(-7) },
    { label: "Last 30 scored days", rows: scored.slice(-30) },
    { label: "All scored days", rows: scored },
  ];

  const pending = scored.length === 0;
  const done = new Set(scores.map((row) => row.target_date));
  const forecastDays = [
    ...new Set(hourly.filter((row) => row.forecast_mw != null).map((row) => localDay(hourStart(row.period_utc)))),
  ].sort();
  const nextToScore = forecastDays.find((day) => !done.has(day));

  return (
    <>
      <PageHeader eyebrow="Live record" title="Scoreboard">
        Each forecast day is scored once, 72 hours after it ends, against reported demand and against
        the operator&apos;s own day-ahead forecast on the same hours.
      </PageHeader>
      <div className="stats">
        <Stat label="Days forecast" value={status.days_forecast} note="since October 12, 2026" />
        <Stat label="Days scored" value={status.days_scored} note={nextToScore ? `next: ${dayLabel(nextToScore)}` : undefined} />
        <Stat
          label="7-day error, outside 09:00-17:00"
          value={pending ? "Pending" : percent(weighted(windows[0].rows, "other_hours_mape"))}
          text={pending}
          note={
            pending
              ? "after the first scored day"
              : `operator ${percent(weighted(windows[0].rows, "operator_other_hours_mape"))}`
          }
        />
        <Stat
          label="Days closer than the operator"
          value={pending ? "Pending" : percent(winRate(scored, "other_hours_mape", "operator_other_hours_mape"), 0)}
          text={pending}
          note={pending ? "after the first scored day" : "outside 09:00-17:00"}
        />
      </div>
      {scored.length === 0 ? (
        <section className="card">
          <Empty title="The first score is on its way">
            A day is scored 72 hours after it ends, using demand as finally reported.
            {nextToScore
              ? ` ${dayLabel(nextToScore)} will be scored on ${dayLabel(addDays(nextToScore, 4))}.`
              : ""}
          </Empty>
        </section>
      ) : (
        <>
          <Card title="Daily error" subtitle="Mean absolute percentage error for each scored day.">
            <DailyMapeChart
              points={scored.map((row) => ({
                day: row.target_date.slice(5),
                ours: row.mape,
                operator: row.operator_mape,
                oursOther: row.other_hours_mape,
                operatorOther: row.operator_other_hours_mape,
              }))}
            />
          </Card>
          <Card title="Running error" subtitle="Weighted by the hours scored on each day." flush>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Window</th>
                    <th>Days</th>
                    <th>Ours, outside midday</th>
                    <th>Operator, outside midday</th>
                    <th>Days we were closer</th>
                    <th>Ours, all hours</th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map((window) => (
                    <tr key={window.label}>
                      <td>{window.label}</td>
                      <td>{window.rows.length}</td>
                      <td className="emphasis">{percent(weighted(window.rows, "other_hours_mape"))}</td>
                      <td>{percent(weighted(window.rows, "operator_other_hours_mape"))}</td>
                      <td>{percent(winRate(window.rows, "other_hours_mape", "operator_other_hours_mape"), 0)}</td>
                      <td>{percent(weighted(window.rows, "mape"))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="Scored days" flush>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Forecast by</th>
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
                      <td>{modelLabel(row.model_version)}</td>
                      <td>
                        {row.hours_scored} of {row.hours}
                      </td>
                      <td className="emphasis">{percent(row.other_hours_mape)}</td>
                      <td>{percent(row.operator_other_hours_mape)}</td>
                      <td>{percent(row.mape)}</td>
                      <td>{megawatts(row.peak_error_mw)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
      <p className="callout">
        <strong>Why the comparison leaves out 09:00-17:00.</strong> Since 2023 the operator&apos;s
        published forecast and its reported demand have diverged in the middle of the day, by about
        7 GW in 2026, so in those hours the two no longer measure the same quantity. Outside them they
        agree, and that is where the head-to-head is fair. Our error over all hours is shown
        separately. A day on which the operator published no forecast is not scored.
      </p>
    </>
  );
}
