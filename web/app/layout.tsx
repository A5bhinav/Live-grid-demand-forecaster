import type { Metadata } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { HealthBadge, type Health } from "@/components/ui";
import { getStatus } from "@/lib/data";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "CAISO demand forecast", template: "%s · CAISO demand forecast" },
  description: "A live day-ahead electricity demand forecast for the California ISO, scored daily.",
};

const REPO = "https://github.com/A5bhinav/Live-grid-demand-forecaster";

function freshness(latestDemand: string | null): { health: Health; label: string } {
  if (!latestDemand) return { health: "critical", label: "No data" };
  const hours = (Date.now() - new Date(latestDemand).getTime()) / 3_600_000;
  if (hours <= 6) return { health: "good", label: "Data current" };
  return { health: hours <= 12 ? "warning" : "critical", label: `Data ${Math.round(hours)} h old` };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const status = await getStatus();
  const fresh = status ? freshness(status.latest_demand_period) : null;
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <div className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="brand">
              <span className="brand-mark" aria-hidden>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M1.5 11c2 0 2.6-6 5-6s2.6 4 4.5 4 2-3.4 3.5-3.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              </span>
              CAISO demand forecast
            </Link>
            <Nav />
            <div className="topbar-meta">
              {fresh ? <HealthBadge health={fresh.health} label={fresh.label} /> : null}
              <a href={REPO}>GitHub ↗</a>
            </div>
          </div>
        </div>
        <main>{children}</main>
        <footer>
          <span>
            Demand and operator forecast: U.S. EIA Form 930. Weather forecasts: Open-Meteo (GFS). Times
            are Pacific.
          </span>
          <span>
            Forecasts are recorded once and cannot be changed. <a href={REPO}>Source and method</a>
          </span>
        </footer>
      </body>
    </html>
  );
}
