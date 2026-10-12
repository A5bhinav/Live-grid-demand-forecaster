export function Tile({
  label,
  value,
  note,
  small = false,
}: {
  label: string;
  value: string;
  note?: string;
  small?: boolean;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={small ? "value small" : "value"}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}
