"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip } from "./ChartTooltip";

export type ModelBar = { model: string; mape: number };

/** One measure across models, so one color; the axis labels carry identity. */
export function ModelBars({ bars, label }: { bars: ModelBar[]; label: string }) {
  return (
    <ResponsiveContainer width="100%" height={bars.length * 44 + 24}>
      <BarChart data={bars} layout="vertical" margin={{ top: 0, right: 56, bottom: 0, left: 0 }}>
        <CartesianGrid horizontal={false} stroke="var(--grid)" />
        <XAxis type="number" hide domain={[0, "auto"]} />
        <YAxis
          type="category"
          dataKey="model"
          width={200}
          tickLine={false}
          axisLine={{ stroke: "var(--axis)" }}
          tick={{ fill: "var(--text-secondary)", fontSize: 13 }}
        />
        <Tooltip
          cursor={{ fill: "var(--grid)", opacity: 0.5 }}
          content={<ChartTooltip format={(value) => `${value.toFixed(2)}%`} />}
        />
        <Bar dataKey="mape" name={label} fill="var(--series-1)" barSize={18} radius={[0, 4, 4, 0]} isAnimationActive={false}>
          <LabelList
            dataKey="mape"
            position="right"
            formatter={(value: unknown) => `${Number(value).toFixed(2)}%`}
            style={{ fill: "var(--text)", fontSize: 13 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
