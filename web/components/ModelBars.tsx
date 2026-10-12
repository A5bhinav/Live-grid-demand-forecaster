"use client";

import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "./ChartTooltip";

export type ModelBar = { model: string; mape: number; role: "ours" | "operator" | "other" };

// The same colors these two carry in every other chart; the rest stay neutral.
const FILL = { ours: "var(--series-1)", operator: "var(--series-2)", other: "var(--border-strong)" };

export function ModelBars({ bars }: { bars: ModelBar[] }) {
  return (
    <ResponsiveContainer width="100%" height={bars.length * 40 + 8}>
      <BarChart data={bars} layout="vertical" margin={{ top: 0, right: 60, bottom: 0, left: 0 }}>
        <XAxis type="number" hide domain={[0, "dataMax"]} />
        <YAxis
          type="category"
          dataKey="model"
          width={210}
          tickLine={false}
          axisLine={false}
          tick={{ fill: "var(--text-secondary)", fontSize: 13 }}
        />
        <Tooltip
          cursor={{ fill: "var(--surface-sunken)" }}
          content={<ChartTooltip format={(value) => `${value.toFixed(2)}%`} />}
        />
        <Bar dataKey="mape" name="MAPE" barSize={16} radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {bars.map((bar) => (
            <Cell key={bar.model} fill={FILL[bar.role]} />
          ))}
          <LabelList
            dataKey="mape"
            position="right"
            offset={10}
            formatter={(value: unknown) => `${Number(value).toFixed(2)}%`}
            style={{ fill: "var(--text)", fontSize: 13, fontWeight: 600 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
