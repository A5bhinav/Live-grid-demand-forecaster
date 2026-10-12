import type { ReactNode } from "react";

export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <header className="page-header">
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p className="lede">{children}</p>
    </header>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  flush = false,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="card-title">{title}</h2>
          {subtitle ? <p className="card-subtitle">{subtitle}</p> : null}
        </div>
        {actions}
      </div>
      <div className={flush ? "card-body flush" : "card-body"}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  note,
  text = false,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  text?: boolean;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={text ? "stat-value text" : "stat-value"}>{value}</div>
      {note ? <div className="stat-note">{note}</div> : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      <p>{children}</p>
    </div>
  );
}

export type Health = "good" | "warning" | "critical";
const HEALTH_LABEL: Record<Health, string> = { good: "OK", warning: "Late", critical: "Stale" };

/** A status is never color alone: the dot always travels with a word. */
export function HealthBadge({ health, label }: { health: Health; label?: string }) {
  return (
    <span className="badge">
      <span className={`status-dot ${health}`} aria-hidden />
      {label ?? HEALTH_LABEL[health]}
    </span>
  );
}
