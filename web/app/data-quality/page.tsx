import type { Metadata } from "next";
import { FlagsChart, type FlagYear } from "@/components/FlagsChart";
import { Unconfigured } from "@/components/Unconfigured";
import { Card, HealthBadge, PageHeader, Stat, type Health } from "@/components/ui";
import { getFlags, getStatus } from "@/lib/data";
import { dayLabel, FLAG_NAMES, hoursAgo, modelLabel, percent, stamp } from "@/lib/format";

export const revalidate = 300;
export const metadata: Metadata = { title: "Data quality" };

function age(iso: string | null, now: Date): number {
  return iso ? (now.getTime() - new Date(iso).getTime()) / 3_600_000 : Infinity;
}
const within = (hours: number, good: number, late: number): Health =>
  hours <= good ? "good" : hours <= late ? "warning" : "critical";

export default async function DataQualityPage() {
  const [flags, status] = await Promise.all([getFlags(), getStatus()]);
  if (!flags || !status) return <Unconfigured />;
  const now = new Date();

  const present = Object.keys(FLAG_NAMES).filter((flag) => flags.some((row) => row.flag === flag));
  const series = present.map((flag) => ({ key: flag, name: FLAG_NAMES[flag] }));
  const blank = () => Object.fromEntries(present.map((flag) => [flag, 0]));
  const byYear = new Map<string, FlagYear>();
  for (const row of flags) {
    const year = row.month.slice(0, 4);
    if (!byYear.has(year)) byYear.set(year, { year, ...blank() });
    const entry = byYear.get(year)!;
    entry[row.flag] = ((entry[row.flag] as number) ?? 0) + row.hours;
  }
  const first = Math.min(...[...byYear.keys()].map(Number), now.getUTCFullYear());
  const years: FlagYear[] = [];
  for (let year = first; year <= now.getUTCFullYear(); year++) {
    years.push(byYear.get(String(year)) ?? { year: String(year), ...blank() });
  }
  const share = (status.demand_hours_flagged / Math.max(status.demand_hours, 1)) * 100;
  const forecastAhead = age(status.latest_forecast_target, now) < 0;

  const pipeline = [
    {
      what: "Reported demand",
      when: `through ${stamp(status.latest_demand_period)} (${hoursAgo(status.latest_demand_period, now)})`,
      health: within(age(status.latest_demand_period, now), 6, 12),
    },
    {
      what: "Operator forecast",
      when: `through ${stamp(status.latest_operator_period)}`,
      health: within(age(status.latest_operator_period, now), 8, 24),
    },
    {
      what: "Weather forecasts",
      when: `through ${stamp(status.latest_weather_valid_time)}`,
      health: within(age(status.latest_weather_valid_time, now), -24, 0),
    },
    {
      what: "Latest forecast",
      when: `issued ${stamp(status.latest_forecast_issued_at)}`,
      health: (forecastAhead ? "good" : "critical") as Health,
    },
    {
      what: "Latest scored day",
      when: status.latest_scored_date ? dayLabel(status.latest_scored_date) : "none yet",
      health: null,
    },
    {
      what: "Active model",
      when: `${modelLabel(status.active_model)}, trained ${stamp(status.active_model_trained_at)}`,
      health: null,
    },
  ];

  return (
    <>
      <PageHeader eyebrow="Inputs" title="Data quality">
        Reported demand is screened before use. Flagged hours are filled in for model inputs and are
        never used for scoring or training.
      </PageHeader>
      <div className="stats">
        <Stat label="Hours screened" value={status.demand_hours.toLocaleString("en-US")} note="reported demand since 2019" />
        <Stat label="Hours flagged" value={status.demand_hours_flagged} note={`${percent(share)} of all hours`} />
        <Stat
          label="Latest reported demand"
          value={hoursAgo(status.latest_demand_period, now)}
          note={stamp(status.latest_demand_period)}
        />
        <Stat label="Days forecast" value={status.days_forecast} note={`${status.days_scored} scored`} />
      </div>
      <Card title="Pipeline" subtitle="What the system has, and how recent it is." flush>
        <ul className="rows">
          {pipeline.map((row) => (
            <li key={row.what}>
              <span className="what">{row.what}</span>
              <span className="when" style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {row.when}
                {row.health ? <HealthBadge health={row.health} /> : null}
              </span>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Flagged hours by year" subtitle="Hours of reported demand that failed a screen.">
        <FlagsChart years={years} series={series} />
        <details>
          <summary>View as a table</summary>
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
      </Card>
      <p className="callout">
        <strong>What the screens catch.</strong> Missing: never published or published empty. Spike: a
        jump away from and back to the surrounding level within six hours. Repeated value: the same
        value three or more times in a day, a sign of a stuck feed. Global outlier: more than ten
        times above or below the usual level. Anomalous region: an hour that looks fine but sits among
        flagged ones.
      </p>
    </>
  );
}
