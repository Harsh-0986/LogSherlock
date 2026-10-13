import Link from "next/link";
import { Suspense } from "react";
import { NavLinks } from "@/components/NavLinks";

/**
 * Navigation shell. Stays a Server Component; the interactive links and model
 * indicator are a client island so prerendering is not blocked.
 */
export function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-edge bg-base/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
        <Link href="/" className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-medium tracking-tight">LogSherlock</span>
          <span className="hidden text-xs text-ink-faint sm:inline">log investigation</span>
        </Link>
        <Suspense fallback={<span className="h-6" />}>
          <NavLinks />
        </Suspense>
      </div>
    </header>
  );
}