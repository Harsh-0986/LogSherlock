"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { api, type ModelStatus } from "@/lib/api";

/**
 * Model configuration status (SPEC §6.2).
 *
 * Read-only by design: the provider is chosen in the backend's environment, and
 * secrets are never sent to or displayed in the browser.
 */
export default function SettingsPage() {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checkedOnce, setCheckedOnce] = useState(false);

  // `manual` distinguishes the initial load from an explicit re-check, so the
  // initial render does not spin a spinner it will immediately replace.
  const [manual, setManual] = useState(false);

  useEffect(() => {
    let cancelled = false;

    void api
      .modelStatus()
      .then((result) => {
        if (cancelled) return;
        setStatus(result);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Could not reach the backend.");
      })
      .finally(() => {
        if (!cancelled) setCheckedOnce(true);
      });

    return () => {
      cancelled = true;
    };
  }, [manual]);

  const checking = manual && !checkedOnce;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Model settings</h1>
      <p className="mt-1.5 text-sm text-ink-dim">
        The provider is configured in the backend environment, not in the browser.
      </p>

      <section className="mt-8 rounded border border-edge bg-panel">
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="text-sm font-medium text-ink">Configured provider</h2>
          <button
            type="button"
            onClick={() => {
              setCheckedOnce(false);
              setManual((v) => !v);
            }}
            disabled={checking}
            className="rounded border border-edge px-2 py-1 text-xs text-ink-dim transition-colors hover:border-edge-bright hover:text-ink disabled:opacity-50"
          >
            {checking ? "Checking…" : "Re-check"}
          </button>
        </div>

        <div className="px-5 py-4">
          {error ? (
            <p className="flex items-start gap-2 text-sm text-critical">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {error}
            </p>
          ) : status === null ? (
            <p className="flex items-center gap-2 text-sm text-ink-dim">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Checking…
            </p>
          ) : (
            <dl className="space-y-2 text-sm">
              <div className="flex gap-3">
                <dt className="w-24 shrink-0 text-ink-faint">Provider</dt>
                <dd className="font-mono text-ink">{status.provider}</dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-24 shrink-0 text-ink-faint">Model</dt>
                <dd className="font-mono text-ink">{status.model}</dd>
              </div>
              <div className="flex gap-3">
                <dt className="w-24 shrink-0 text-ink-faint">Status</dt>
                <dd>
                  {status.available ? (
                    <span className="inline-flex items-center gap-1.5 text-accent">
                      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                      reachable
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-high">
                      <AlertCircle className="h-3.5 w-3.5" aria-hidden />
                      {status.detail || "unreachable"}
                    </span>
                  )}
                </dd>
              </div>
            </dl>
          )}
        </div>
      </section>

      {!status?.available && (
        <section className="mt-6 rounded border sev-ring-medium bg-panel px-5 py-4">
          <h2 className="text-sm font-medium text-medium">
            Analyses still work without a model
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-ink-dim">
            Parsing, normalization, grouping and severity ranking are entirely
            deterministic. Investigations complete with status{" "}
            <span className="font-mono text-ink">partial</span> and report those groups
            without AI hypotheses.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">
            To enable AI analysis, set <code className="font-mono text-ink">MODEL_PROVIDER</code>{" "}
            and the matching credentials in the backend{"'"}s{" "}
            <code className="font-mono text-ink">.env</code>, then restart it. See the
            README for each provider.
          </p>
        </section>
      )}

      <section className="mt-6 rounded border border-edge bg-panel px-5 py-4">
        <h2 className="text-sm font-medium text-ink">Where your logs go</h2>
        <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-ink-dim">
          <li>
            Logs are processed on your machine by the backend. Nothing is sent to an
            unconfigured service.
          </li>
          <li>
            Redaction runs before evidence is stored or sent, and replaces secrets with
            placeholders like <code className="font-mono text-ink">[REDACTED_TOKEN]</code>.
          </li>
          <li>
            Raw logs are not written to the database unless{" "}
            <code className="font-mono text-ink">PERSIST_RAW_LOGS</code> is enabled.
          </li>
          <li>
            Pattern redaction is best-effort and cannot detect every secret. Review logs
            before sharing them.
          </li>
        </ul>
      </section>
    </div>
  );
}