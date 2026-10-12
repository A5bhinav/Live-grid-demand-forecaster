"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "./ChartTooltip";

export type DailyPoint = {
  day: string;
  ours: number | null;
  operator: number | null;
  oursOther: number | null;
  operatorOther: number | null;
};

const SCOPES = [
  { key: "other", label: "Outside 09:00-17:00" },
  { key: "all", label: "All hours" },
] as const;

export function DailyMapeChart({ points }: { points: DailyPoint[] }) {
  const [scope, setScope] = useState<"other" | "all">("other");
  const keys = scope === "all" ? (["ours", "operator"] as const) : (["oursOther", "operatorOther"] as const);
  const dots = points.length < 20;
  const lines = [
    { key: keys[0], name: "Our forecast", color: "var(--series-1)" },
    { key: keys[1], name: "Operator forecast", color: "var(--series-2)" },
  ];
  return (
    <div>
      <div className="controls" style={{ justifyContent: "space-between", marginBottom: 14 }}>
        <div className="legend" style={{ margin: 0 }}>
          {lines.map((line) => (
            <span key={line.name}>
              <span className="key" style={{ borderTopColor: line.color }} />
              {line.name}
            </span>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Hours included">
          {SCOPES.map((option) => (
            <button key={option.key} aria-pressed={scope === option.key} onClick={() => setScope(option.key)}>
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="day"
            tickLine={false}
            tickMargin={8}
            minTickGap={24}
            axisLine={{ stroke: "var(--axis)" }}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          />
          <YAxis
            width={44}
            tickLine={false}
            axisLine={false}
            domain={[0, "auto"]}
            tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            tickFormatter={(value: number) => `${value}%`}
          />
          <Tooltip
            cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
            content={<ChartTooltip format={(value) => `${value.toFixed(2)}%`} />}
          />
          {lines.map((line) => (
            <Line
              key={line.name}
              type="linear"
              dataKey={line.key}
              name={line.name}
              stroke={line.color}
              strokeWidth={2}
              dot={dots ? { r: 3, strokeWidth: 0, fill: line.color } : false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
