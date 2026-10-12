"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "./ChartTooltip";

export type DayRow = {
  hour: string;
  forecast: number | null;
  operator: number | null;
  actual: number | null;
};
export type Day = { day: string; label: string; rows: DayRow[] };

const SERIES = [
  { key: "forecast", name: "Our forecast", color: "var(--series-1)" },
  { key: "operator", name: "Operator forecast", color: "var(--series-2)" },
  { key: "actual", name: "Actual demand", color: "var(--actual)" },
] as const;
type Key = (typeof SERIES)[number]["key"];

const mw = (value: number | null) => (value == null ? "" : Math.round(value).toLocaleString("en-US"));

function peak(rows: DayRow[], key: Key): DayRow | null {
  const present = rows.filter((row) => row[key] != null);
  if (!present.length) return null;
  return present.reduce((best, row) => (row[key]! > best[key]! ? row : best));
}

/** Round axis bounds and evenly spaced ticks, in MW. */
function axis(rows: DayRow[], keys: Key[]): { domain: [number, number]; ticks: number[] } {
  const values = rows.flatMap((row) => keys.map((key) => row[key])).filter((v): v is number => v != null);
  const span = Math.max(...values) - Math.min(...values);
  const step = span > 24_000 ? 10_000 : span > 9_000 ? 5_000 : 2_000;
  const low = Math.floor(Math.min(...values) / step) * step;
  const high = Math.ceil(Math.max(...values) / step) * step;
  const ticks = [];
  for (let tick = low; tick <= high; tick += step) ticks.push(tick);
  return { domain: [low, high], ticks };
}

export function HourlyChart({ days, initialDay }: { days: Day[]; initialDay: string }) {
  const [selected, setSelected] = useState(initialDay);
  const index = Math.max(0, days.findIndex((candidate) => candidate.day === selected));
  const day = days[index];
  const shown = SERIES.filter((series) => day.rows.some((row) => row[series.key] != null));
  const { domain, ticks } = axis(day.rows, shown.map((series) => series.key));

  return (
    <div>
      <div className="controls" style={{ marginBottom: 18 }}>
        <button className="icon" aria-label="Previous day" disabled={index === 0} onClick={() => setSelected(days[index - 1].day)}>
          ‹
        </button>
        <select aria-label="Day" value={day.day} onChange={(event) => setSelected(event.target.value)}>
          {[...days].reverse().map((option) => (
            <option key={option.day} value={option.day}>
              {option.label}
            </option>
          ))}
        </select>
        <button className="icon" aria-label="Next day" disabled={index === days.length - 1} onClick={() => setSelected(days[index + 1].day)}>
          ›
        </button>
      </div>
      {/* With a single series the peak is already in the tiles above. */}
      <div className="kvs" hidden={shown.length < 2}>
        {shown.map((series) => {
          const top = peak(day.rows, series.key);
          return (
            <div className="kv" key={series.key}>
              <div className="k">
                <span className="key" style={{ borderTopColor: series.color }} />
                {series.name}, peak
              </div>
              <div className="v">
                {top ? `${(top[series.key]! / 1000).toFixed(1)} GW` : "n/a"}
                {top ? <small>at {top.hour}</small> : null}
              </div>
            </div>
          );
        })}
      </div>
      {shown.length === 1 ? (
        <div className="legend">
          <span>
            <span className="key" style={{ borderTopColor: shown[0].color }} />
            {shown[0].name}
          </span>
        </div>
      ) : null}
      <ResponsiveContainer width="100%" height={340}>
        <LineChart data={day.rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="hour"
            interval={2}
            tickLine={false}
            tickMargin={8}
            axisLine={{ stroke: "var(--axis)" }}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            width={54}
            tickLine={false}
            axisLine={false}
            domain={domain}
            ticks={ticks}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            tickFormatter={(value: number) => `${value / 1000} GW`}
          />
          <Tooltip
            cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
            content={
              <ChartTooltip
                format={(value) => `${Math.round(value).toLocaleString("en-US")} MW`}
                title={(hour) => `${day.label}, hour starting ${hour}`}
              />
            }
          />
          {shown.map((series) => (
            <Line
              key={series.key}
              type="monotone"
              dataKey={series.key}
              name={series.name}
              stroke={series.color}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <details>
        <summary>View as a table</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Hour starting</th>
                {shown.map((series) => (
                  <th key={series.key}>{series.name} (MW)</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {day.rows.map((row, position) => (
                <tr key={position}>
                  <td>{row.hour}</td>
                  {shown.map((series) => (
                    <td key={series.key}>{mw(row[series.key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
