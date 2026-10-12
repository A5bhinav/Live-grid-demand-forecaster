import { HourlyChart, type Day } from "@/components/HourlyChart";
import { Unconfigured } from "@/components/Unconfigured";
import { Card, Empty, PageHeader, Stat } from "@/components/ui";
import { getHourly, getStatus } from "@/lib/data";
import { dayLabel, gigawatts, hourStart, localDay, localHour, modelLabel, stamp } from "@/lib/format";

export const revalidate = 300;

export default async function ForecastPage() {
  const [hourly, status] = await Promise.all([getHourly(), getStatus()]);
  if (!hourly || !status) return <Unconfigured />;

  const byDay = new Map<string, Day>();
  for (const row of hourly) {
    const start = hourStart(row.period_utc);
    const key = localDay(start);
    if (!byDay.has(key)) byDay.set(key, { day: key, label: dayLabel(key), rows: [] });
    byDay.get(key)!.rows.push({
      hour: localHour(start),
      forecast: row.forecast_mw,
      operator: row.operator_mw,
      actual: row.actual_mw,
    });
  }
  const days = [...byDay.values()].slice(-15);
  const latest = days.filter((day) => day.rows.some((row) => row.forecast != null)).at(-1);

  const header = (
    <PageHeader eyebrow="Live forecast" title="Day-ahead electricity demand">
      Each morning before 10:00 Pacific this system forecasts California ISO demand for every hour of
      the next day. A forecast is recorded once and cannot be changed afterwards.
    </PageHeader>
  );
  if (!latest) {
    return (
      <>
        {header}
        <section className="card">
          <Empty title="No forecast yet">The first forecast will appear here once it is issued.</Empty>
        </section>
      </>
    );
  }

  const forecasts = latest.rows.filter((row) => row.forecast != null);
  const peak = forecasts.reduce((best, row) => (row.forecast! > best.forecast! ? row : best));
  const trough = forecasts.reduce((best, row) => (row.forecast! < best.forecast! ? row : best));
  const issued = hourly.findLast((row) => row.forecast_mw != null);

  return (
    <>
      {header}
      <div className="stats">
        <Stat label={`Peak, ${latest.label}`} value={gigawatts(peak.forecast)} note={`hour starting ${peak.hour}`} />
        <Stat label={`Low, ${latest.label}`} value={gigawatts(trough.forecast)} note={`hour starting ${trough.hour}`} />
        <Stat label="Issued" value={stamp(issued?.issued_at ?? null)} text note="stamped by the database clock" />
        <Stat
          label="Forecast by"
          value={modelLabel(issued?.model_version)}
          text
          note={<span className="mono">{issued?.model_version}</span>}
        />
      </div>
      <Card
        title="Hourly demand"
        subtitle="Our forecast, the operator's own day-ahead forecast and reported demand. The last two appear once the day has happened."
      >
        <HourlyChart days={days} initialDay={latest.day} />
      </Card>
      <p className="note">
        Actual demand is what the grid operator reports to the U.S. Energy Information Administration.
        Hours whose reported value failed screening are left out.
      </p>
    </>
  );
}
