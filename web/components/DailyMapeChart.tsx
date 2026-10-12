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

export function DailyMapeChart({ points }: { points: DailyPoint[] }) {
  const [scope, setScope] = useState<"other" | "all">("other");
  const keys = scope === "all" ? (["ours", "operator"] as const) : (["oursOther", "operatorOther"] as const);
  return (
    <div>
      <div className="controls">
        <label htmlFor="scope">Hours</label>
        <select id="scope" value={scope} onChange={(event) => setScope(event.target.value as "other" | "all")}>
          <option value="other">Outside 09:00-17:00 Pacific</option>
          <option value="all">All hours</option>
        </select>
      </div>
      <div className="legend">
        <span>
          <span className="key" style={{ borderTopColor: "var(--series-1)" }} />
          Our forecast
        </span>
        <span>
          <span className="key" style={{ borderTopColor: "var(--series-2)" }} />
          Operator forecast
        </span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis
            dataKey="day"
            tickLine={false}
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
          <Line
            type="linear"
            dataKey={keys[0]}
            name="Our forecast"
            stroke="var(--series-1)"
            strokeWidth={2}
            dot={points.length < 20 ? { r: 3, strokeWidth: 0, fill: "var(--series-1)" } : false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
          <Line
            type="linear"
            dataKey={keys[1]}
            name="Operator forecast"
            stroke="var(--series-2)"
            strokeWidth={2}
            dot={points.length < 20 ? { r: 3, strokeWidth: 0, fill: "var(--series-2)" } : false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
