import { HourlyChart, type Day } from "@/components/HourlyChart";
import { Tile } from "@/components/Tile";
import { Unconfigured } from "@/components/Unconfigured";
import { getHourly, getStatus } from "@/lib/data";
import { dayLabel, gigawatts, hourStart, localDay, localHour, stamp } from "@/lib/format";

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
  const forecastDays = days.filter((day) => day.rows.some((row) => row.forecast != null));
  const latest = forecastDays.at(-1);
  if (!latest) {
    return (
      <div className="card">
        <p className="empty">No forecast has been issued yet.</p>
      </div>
    );
  }
  const peak = latest.rows.reduce((best, row) => ((row.forecast ?? 0) > (best.forecast ?? 0) ? row : best));
  const latestRow = hourly.findLast((row) => row.forecast_mw != null);

  return (
    <>
      <h2>Day-ahead forecast</h2>
      <p className="lede">
        Every morning before 10:00 Pacific this system forecasts California ISO demand for each hour
        of the next day. A forecast is recorded once and cannot be changed afterwards.
      </p>
      <div className="tiles">
        <Tile label={`Forecast peak, ${latest.label}`} value={gigawatts(peak.forecast)} note={`hour starting ${peak.hour} Pacific`} />
        <Tile label="Issued" value={stamp(latestRow?.issued_at ?? null)} small />
        <Tile label="Model" value={latestRow?.model_version ?? "none"} small />
        <Tile label="Days forecast so far" value={String(status.days_forecast)} />
      </div>
      <div className="card">
        <h3>Forecast, operator forecast and actual demand by hour</h3>
        <HourlyChart days={days} initialDay={latest.day} />
      </div>
      <p className="note">
        Actual demand is what the grid operator reports to the U.S. Energy Information Administration.
        Hours whose reported value failed screening are left out. The operator forecast is the
        operator&apos;s own published day-ahead forecast; it appears only once the day has happened.
      </p>
    </>
  );
}
