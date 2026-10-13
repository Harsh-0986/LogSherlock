"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ModelStatus } from "@/lib/api";
import { ThemeToggle } from "@/components/ThemeToggle";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/analyze", label: "Analyze" },
  { href: "/settings", label: "Settings" },
];

/**
 * The interactive half of the navigation.
 *
 * `usePathname` suspends during prerender, so this is a separate Client
 * Component island rather than part of the server-rendered shell.
 */
export function NavLinks() {
  const pathname = usePathname();
  const [model, setModel] = useState<ModelStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .modelStatus()
      .then((status) => {
        if (!cancelled) setModel(status);
      })
      .catch(() => {
        if (!cancelled) setModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <nav aria-label="Main" className="flex items-center gap-1">
        {LINKS.map((link) => {
          const active =
            link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? "page" : undefined}
              className={`rounded px-2.5 py-1 text-sm transition-colors ${
                active ? "bg-raised text-ink" : "text-ink-dim hover:text-ink"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        <ModelIndicator status={model} />
        <ThemeToggle />
      </div>
    </>
  );
}

/**
 * Reports the three states that matter: a reachable model, a configured but
 * unreachable one, and no backend at all.
 */
function ModelIndicator({ status }: { status: ModelStatus | null }) {
  if (status === null) {
    return (
      <span className="font-mono text-xs text-ink-faint" title="Backend unreachable">
        backend offline
      </span>
    );
  }

  const dot = status.available ? "bg-accent" : "bg-high";
  const label = status.available
    ? `${status.provider}/${status.model}`
    : `${status.provider} unreachable`;

  return (
    <span
      className="inline-flex items-center gap-2 font-mono text-xs text-ink-dim"
      title={status.detail || label}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
      {label}
    </span>
  );
}