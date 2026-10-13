"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, FileSearch, Lock, Trash2 } from "lucide-react";
import { api, type InvestigationListItem } from "@/lib/api";
import { formatTime, pluralise } from "@/lib/format";
import { Footer } from "@/components/Footer";

export default function HomePage() {
  const [recent, setRecent] = useState<InvestigationListItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api
      .listInvestigations({ limit: 5 })
      .then((page) => {
        if (!cancelled) setRecent(page.items);
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="mx-auto max-w-6xl px-6 py-16 sm:py-24">
        <section className="max-w-3xl">
          <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
            Your logs already contain the answer.
            <span className="block text-ink-faint">LogSherlock finds it.</span>
          </h1>

          <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-dim">
            Paste a log file and get back the errors that actually repeat, ranked by
            severity, with every conclusion tied back to the line that supports it.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/analyze"
              className="inline-flex items-center gap-2 rounded bg-accent px-4 py-2 text-sm font-medium text-base transition-opacity hover:opacity-90"
            >
              Analyze logs
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
            <Link
              href="/analyze?sample=1"
              className="inline-flex items-center gap-2 rounded border border-edge-bright px-4 py-2 text-sm text-ink transition-colors hover:border-ink-faint"
            >
              Try a sample log
            </Link>
          </div>

          <p className="mt-4 text-xs text-ink-faint">
            No account. Nothing is uploaded anywhere you have not configured.
          </p>
        </section>

        {/* How it works: a genuine sequence, so the structure is honest. */}
        <section className="mt-20 border-t border-edge pt-10">
          <h2 className="text-sm font-medium text-ink">How an investigation runs</h2>
          <ol className="mt-6 grid gap-8 sm:grid-cols-3">
            {[
              {
                title: "Group deterministically",
                body: "Timestamps, request ids and memory addresses are normalised away, so one bug seen 40 times becomes one group instead of forty.",
              },
              {
                title: "Then ask the model",
                body: "Only a bounded, redacted slice of evidence is sent. The model reads it as data and never as instructions.",
              },
              {
                title: "Check every claim",
                body: "Citations are resolved against real lines before they reach you. A claim that cites nothing gets dropped.",
              },
            ].map((item, index) => (
              <li key={item.title}>
                <span className="font-mono text-xs text-ink-faint">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3 className="mt-2 text-base font-medium text-ink">{item.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-ink-dim">{item.body}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Recent investigations */}
        <section className="mt-20 border-t border-edge pt-10">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="text-sm font-medium text-ink">Recent investigations</h2>
            {recent !== null && recent.length > 0 && (
              <button
                type="button"
                onClick={async () => {
                  await Promise.all(
                    recent.map((item) => api.deleteInvestigation(item.id).catch(() => {})),
                  );
                  setRecent([]);
                }}
                className="inline-flex items-center gap-1.5 text-xs text-ink-faint transition-colors hover:text-ink-dim"
              >
                <Trash2 className="h-3 w-3" aria-hidden />
                Clear all
              </button>
            )}
          </div>

          {recent === null ? (
            <p className="mt-4 text-sm text-ink-faint">Loading…</p>
          ) : recent.length === 0 ? (
            <div className="mt-4 rounded border border-dashed border-edge px-5 py-8 text-center">
              <FileSearch className="mx-auto h-5 w-5 text-ink-faint" aria-hidden />
              <p className="mt-3 text-sm text-ink-dim">No investigations yet.</p>
              <Link
                href="/analyze?sample=1"
                className="mt-1 inline-block text-sm text-accent hover:underline"
              >
                Run one on a sample log
              </Link>
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-edge border-y border-edge">
              {recent.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/investigations/${item.id}`}
                    className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3 transition-colors hover:bg-panel"
                  >
                    <span className="font-mono text-xs text-ink-faint">
                      {formatTime(item.created_at)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">
                      {item.summary ?? "No summary"}
                    </span>
                    <span className="font-mono text-xs text-ink-faint">
                      {item.error_group_count > 0
                        ? pluralise(item.error_group_count, "group")
                        : "no groups"}
                    </span>
                    <StatusPill status={item.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="mt-20 flex items-start gap-3 border-t border-edge pt-10">
          <Lock className="mt-0.5 h-4 w-4 shrink-0 text-ink-faint" aria-hidden />
          <div className="max-w-2xl text-sm leading-relaxed text-ink-dim">
            <p className="font-medium text-ink">Privacy, stated plainly.</p>
            <p className="mt-1.5">
              Pattern-based redaction runs before anything is stored or sent, so tokens,
              connection strings and emails are replaced with placeholders. Raw logs are
              not written to the database by default. Pattern matching is best-effort and
              cannot catch every secret — review your logs before sharing them.
            </p>
          </div>
        </section>
      </div>

      <Footer />
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "failed"
      ? "text-critical sev-ring-critical"
      : status === "partial"
        ? "text-medium sev-ring-medium"
        : status === "completed"
          ? "text-ink-dim border-edge-bright"
          : "text-accent sev-ring-accent";

  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>
      {status}
    </span>
  );
}