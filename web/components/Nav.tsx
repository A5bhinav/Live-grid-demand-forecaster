"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Forecast" },
  { href: "/scoreboard", label: "Scoreboard" },
  { href: "/backtest", label: "Backtest" },
  { href: "/data-quality", label: "Data quality" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Sections">
      {LINKS.map((link) => (
        <Link key={link.href} href={link.href} aria-current={pathname === link.href ? "page" : undefined}>
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
