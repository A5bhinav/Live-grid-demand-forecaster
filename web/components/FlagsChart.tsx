"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "./ChartTooltip";

export type FlagSeries = { key: string; name: string };
export type FlagYear = { year: string } & Record<string, number | string>;

// Fixed order: a flag keeps its color whichever flags are present.
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)", "var(--series-7)", "var(--series-8)"];

export function FlagsChart({ years, series }: { years: FlagYear[]; series: FlagSeries[] }) {
  return (
    <div>
      <div className="legend">
        {series.map((flag, index) => (
          <span key={flag.key}>
            <span className="swatch" style={{ background: COLORS[index] }} />
            {flag.name}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={years} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="year"
            tickLine={false}
            axisLine={{ stroke: "var(--axis)" }}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            width={40}
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <Tooltip
            cursor={{ fill: "var(--grid)", opacity: 0.5 }}
            content={<ChartTooltip format={(value) => `${value} h`} />}
          />
          {series.map((flag, index) => (
            <Bar
              key={flag.key}
              dataKey={flag.key}
              name={flag.name}
              stackId="flags"
              fill={COLORS[index]}
              stroke="var(--surface)"
              strokeWidth={2}
              barSize={24}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
