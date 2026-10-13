"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Loader2, ShieldCheck } from "lucide-react";
import { api, type ModelStatus } from "@/lib/api";

/**
 * Phase 1 shell: proves the frontend and backend communicate.
 * Replaced by the full landing page in Phase 4.
 */
export default function Home() {
  const [backend, setBackend] = useState<"checking" | "up" | "down">("checking");
  const [model, setModel] = useState<ModelStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        await api.health();
        if (!cancelled) setBackend("up");
      } catch {
        if (!cancelled) setBackend("down");
        return;
      }
      try {
        const status = await api.modelStatus();
        if (!cancelled) setModel(status);
      } catch {
        if (!cancelled) setModel(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-3">
        <h1 className="text-4xl font-semibold tracking-tight">LogSherlock</h1>
        <p className="text-neutral-400">
          Turn raw application logs into an actionable debugging report, backed by evidence.
        </p>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-900/50 p-5">
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-500">
          Phase 1 — connectivity check
        </h2>
        <dl className="space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <dt className="w-28 text-neutral-500">Backend</dt>
            <dd>
              {backend === "checking" && (
                <span className="inline-flex items-center gap-2 text-neutral-400">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> checking
                </span>
              )}
              {backend === "up" && (
                <span className="inline-flex items-center gap-2 text-emerald-400">
                  <Activity className="h-3.5 w-3.5" /> reachable
                </span>
              )}
              {backend === "down" && (
                <span className="inline-flex items-center gap-2 text-red-400">
                  unreachable — start the backend on port 8000
                </span>
              )}
            </dd>
          </div>

          <div className="flex items-center gap-2">
            <dt className="w-28 text-neutral-500">Model</dt>
            <dd className="font-mono text-xs text-neutral-300">
              {model ? (
                <>
                  {model.provider} / {model.model}{" "}
                  <span className={model.available ? "text-emerald-400" : "text-amber-400"}>
                    {model.available ? "available" : "unavailable"}
                  </span>
                </>
              ) : (
                <span className="text-neutral-500">not reported</span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      <footer className="flex items-center gap-2 text-xs text-neutral-600">
        <ShieldCheck className="h-3.5 w-3.5" />
        Logs are processed locally and redacted before any model submission.
        <Link href="/analyze" className="ml-auto text-neutral-400 hover:text-neutral-200">
          Analyze logs →
        </Link>
      </footer>
    </main>
  );
}