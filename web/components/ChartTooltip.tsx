"use client";

type Entry = { name?: string; value?: number | null; color?: string };

/** One readout for every series at the hovered position: value first, series name after. */
export function ChartTooltip({
  active,
  payload,
  label,
  format,
  title,
}: {
  active?: boolean;
  payload?: Entry[];
  label?: string | number;
  format: (value: number) => string;
  title?: (label: string | number) => string;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((entry) => entry.value != null);
  if (!rows.length) return null;
  return (
    <div className="tooltip">
      <div className="title">{title && label != null ? title(label) : label}</div>
      {rows.map((entry) => (
        <div className="row" key={entry.name}>
          <span>
            <span className="key" style={{ borderTop: `2px solid ${entry.color}`, display: "inline-block", width: 14, marginRight: 6, verticalAlign: "middle" }} />
            {entry.name}
          </span>
          <strong>{format(entry.value as number)}</strong>
        </div>
      ))}
    </div>
  );
}
