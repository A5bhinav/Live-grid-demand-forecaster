import { FlagsChart, type FlagYear } from "@/components/FlagsChart";
import { Tile } from "@/components/Tile";
import { Unconfigured } from "@/components/Unconfigured";
import { getFlags, getStatus } from "@/lib/data";
import { FLAG_NAMES, hoursAgo, percent, stamp } from "@/lib/format";

export const revalidate = 300;

export default async function DataQualityPage() {
  const [flags, status] = await Promise.all([getFlags(), getStatus()]);
  if (!flags || !status) return <Unconfigured />;
  const now = new Date();

  const present = Object.keys(FLAG_NAMES).filter((flag) => flags.some((row) => row.flag === flag));
  const series = present.map((flag) => ({ key: flag, name: FLAG_NAMES[flag] }));
  const byYear = new Map<string, FlagYear>();
  for (const row of flags) {
    const year = row.month.slice(0, 4);
    if (!byYear.has(year)) byYear.set(year, { year, ...Object.fromEntries(present.map((flag) => [flag, 0])) });
    const entry = byYear.get(year)!;
    entry[row.flag] = ((entry[row.flag] as number) ?? 0) + row.hours;
  }
  const first = Math.min(...[...byYear.keys()].map(Number), now.getUTCFullYear());
  const years: FlagYear[] = [];
  for (let year = first; year <= now.getUTCFullYear(); year++) {
    const key = String(year);
    years.push(byYear.get(key) ?? { year: key, ...Object.fromEntries(present.map((flag) => [flag, 0])) });
  }
  const share = (status.demand_hours_flagged / Math.max(status.demand_hours, 1)) * 100;

  return (
    <>
      <h2>Data quality</h2>
      <p className="lede">
        Reported demand is screened before use. Flagged hours are filled in for model inputs and are
        never used for scoring.
      </p>
      <div className="tiles">
        <Tile label="Latest reported demand" value={hoursAgo(status.latest_demand_period, now)} note={stamp(status.latest_demand_period)} />
        <Tile label="Latest forecast issued" value={hoursAgo(status.latest_forecast_issued_at, now)} note={stamp(status.latest_forecast_issued_at)} />
        <Tile label="Weather forecasts through" value={stamp(status.latest_weather_valid_time)} small />
        <Tile label="Hours flagged" value={percent(share)} note={`${status.demand_hours_flagged} of ${status.demand_hours.toLocaleString("en-US")} since 2019`} />
      </div>
      <div className="card">
        <h3>Flagged hours of reported demand, by year</h3>
        <FlagsChart years={years} series={series} />
        <details>
          <summary>Show as a table</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Year</th>
                  {series.map((flag) => (
                    <th key={flag.key}>{flag.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {years.map((year) => (
                  <tr key={year.year}>
                    <td>{year.year}</td>
                    {series.map((flag) => (
                      <td key={flag.key}>{year[flag.key]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
      <p className="note">
        Missing: never published or published empty. Spike: a jump away from and back to the
        surrounding level within six hours. Repeated value: the same value three or more times in a
        day, a sign of a stuck feed. Global outlier: more than ten times above or below the usual
        level. Anomalous region: an hour that looks fine but sits among flagged ones. Active model:{" "}
        {status.active_model ?? "seasonal naive baseline"}.
      </p>
    </>
  );
}
