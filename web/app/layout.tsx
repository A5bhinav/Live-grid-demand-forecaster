import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "CAISO demand forecaster",
  description: "A live day-ahead electricity demand forecast for the California ISO, scored daily.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="site">
            <h1>CAISO demand forecaster</h1>
            <nav>
              <Link href="/">Forecast</Link>
              <Link href="/scoreboard">Scoreboard</Link>
              <Link href="/backtest">Backtest</Link>
              <Link href="/data-quality">Data quality</Link>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
