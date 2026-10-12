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

const mw = (value: number | null) => (value == null ? "" : Math.round(value).toLocaleString("en-US"));

export function HourlyChart({ days, initialDay }: { days: Day[]; initialDay: string }) {
  const [selected, setSelected] = useState(initialDay);
  const day = days.find((candidate) => candidate.day === selected) ?? days[days.length - 1];
  const shown = SERIES.filter((series) => day.rows.some((row) => row[series.key] != null));

  return (
    <div>
      <div className="controls">
        <label htmlFor="day">Day</label>
        <select id="day" value={day.day} onChange={(event) => setSelected(event.target.value)}>
          {[...days].reverse().map((option) => (
            <option key={option.day} value={option.day}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <div className="legend">
        {shown.map((series) => (
          <span key={series.key}>
            <span className="key" style={{ borderTopColor: series.color }} />
            {series.name}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={340}>
        <LineChart data={day.rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="hour"
            interval={2}
            tickLine={false}
            axisLine={{ stroke: "var(--axis)" }}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            width={60}
            tickLine={false}
            axisLine={false}
            domain={["auto", "auto"]}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            tickFormatter={(value: number) => `${(value / 1000).toFixed(0)} GW`}
          />
          <Tooltip
            cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
            content={
              <ChartTooltip
                format={(value) => `${Math.round(value).toLocaleString("en-US")} MW`}
                title={(hour) => `Hour starting ${hour} Pacific`}
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
        <summary>Show as a table</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Hour starting (Pacific)</th>
                {shown.map((series) => (
                  <th key={series.key}>{series.name} (MW)</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {day.rows.map((row) => (
                <tr key={row.hour + day.rows.indexOf(row)}>
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
