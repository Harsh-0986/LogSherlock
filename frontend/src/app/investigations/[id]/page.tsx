"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileSearch,
  Loader2,
} from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { ErrorGroup, Finding, InvestigationReport, ProcessingStep } from "@/lib/types";
import { SEVERITY_RANK } from "@/lib/types";
import {
  findingToMarkdown,
  formatDuration,
  formatTime,
  indexEvidence,
  pluralise,
  reportToMarkdown,
  severityColor,
} from "@/lib/format";
import { CopyButton } from "@/components/CopyButton";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { SeverityBar, SeverityMark } from "@/components/Severity";
import { AnalysisProgress } from "@/components/AnalysisProgress";

const TERMINAL = new Set(["completed", "partial", "failed"]);

function InvestigationView() {
  const params = useParams<{ id: string }>();
  const id = params?.id as string | undefined;

  const [report, setReport] = useState<InvestigationReport | null>(null);
  const [liveSteps, setLiveSteps] = useState<ProcessingStep[]>([]);
  const [running, setRunning] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);

  // Mirrors the backend status so the interval always sees the current value
  // without being torn down and rebuilt on every render.
  const statusRef = useRef<string>("queued");

  const loadReport = useCallback(async () => {
    if (!id) return null;
    const data = await api.investigationReport<InvestigationReport>(id);
    statusRef.current = data.status;
    setReport(data);
    setLiveSteps(data.processing_steps);
    setRunning(!TERMINAL.has(data.status));
    return data;
  }, [id]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    // The status endpoint carries the live step transitions, so it is polled
    // while the run is in flight; the full report is fetched once it settles.
    const poll = async () => {
      try {
        const status = await api.investigationStatus(id);
        if (cancelled) return;
        statusRef.current = status.status;
        setLiveSteps(status.steps);
        setRunning(!TERMINAL.has(status.status));

        if (TERMINAL.has(status.status)) {
          await loadReport();
          if (!cancelled) setError(null);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      }
    };

    const start = async () => {
      try {
        await loadReport();
        if (!cancelled) void poll();
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? err.message : "This investigation could not be loaded.",
        );
      }
    };

    void start();
    const timer = setInterval(() => {
      if (!TERMINAL.has(statusRef.current)) void poll();
    }, 900);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [id, loadReport]);

  const retry = useCallback(async () => {
    if (!id) return;
    setRetrying(true);
    setError(null);
    statusRef.current = "running";
    setRunning(true);
    try {
      await api.retryAi(id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The retry could not be started.");
      setRunning(false);
      statusRef.current = report?.status ?? "partial";
    } finally {
      setRetrying(false);
    }
  }, [id, report?.status]);

  const evidenceIndex = useMemo(() => (report ? indexEvidence(report) : new Map()), [report]);
  const selectedEvidence = useMemo(
    () => report?.evidence.find((e) => e.id === selectedEvidenceId) ?? null,
    [report, selectedEvidenceId],
  );

  // A finding may cite a line the evidence bundle does not carry. Clicking it
  // must not be a silent no-op.
  const selectedCitationMissing =
    selectedEvidenceId !== null && !selectedEvidence;

  if (error) {
    return (
      <Shell>
        <div className="rounded border border-edge bg-panel px-5 py-8 text-center">
          <AlertTriangle className="mx-auto h-5 w-5 text-high" aria-hidden />
          <p className="mt-3 text-sm text-ink">{error}</p>
          <div className="mt-4 flex justify-center gap-3">
            <button
              type="button"
              onClick={() => void loadReport().catch(() => setError('Could not reload.'))}
              className="rounded border border-edge-bright px-3 py-1.5 text-sm text-ink hover:border-ink-faint"
            >
              Try again
            </button>
            <Link
              href="/analyze"
              className="rounded border border-edge-bright px-3 py-1.5 text-sm text-ink hover:border-ink-faint"
            >
              New investigation
            </Link>
          </div>
        </div>
      </Shell>
    );
  }

  if (!report) {
    return (
      <Shell>
        <p className="flex items-center gap-2 text-sm text-ink-dim">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading report…
        </p>
      </Shell>
    );
  }

  const sortedGroups = [...report.error_groups].sort(
    (a, b) =>
      SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || b.count - a.count,
  );
  const maxCount = sortedGroups[0]?.count ?? 0;

  const markdown = reportToMarkdown(report);

  return (
    <Shell>
      <div className="flex flex-col gap-8 lg:flex-row">
        <div className="min-w-0 flex-1">
          {/* --- Header ------------------------------------------------- */}
          <header className="border-b border-edge pb-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="font-mono text-xs text-ink-faint">{report.id}</p>
                <h1 className="mt-1 text-2xl font-semibold tracking-tight">
                  Investigation report
                </h1>
              </div>
              <div className="flex items-center gap-2">
                <CopyButton value={markdown} label="Copy as Markdown" />
                <Link
                  href="/analyze"
                  className="rounded border border-edge px-2 py-1 text-xs text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
                >
                  New analysis
                </Link>
              </div>
            </div>

            <SummaryBar report={report} />

            {report.status === "failed" && report.error_message && (
              <p className="mt-4 rounded border sev-ring-critical bg-panel px-3 py-2 text-sm text-critical">
                {report.error_message}
              </p>
            )}

            {!report.ai_analysis_performed && report.ai_skipped_reason && (
              <div className="mt-4 rounded border sev-ring-medium bg-panel px-3 py-2.5">
                <p className="text-sm font-medium text-medium">No AI analysis in this report</p>
                <p className="mt-1 text-sm text-ink-dim">{report.ai_skipped_reason}</p>
                <p className="mt-1.5 text-xs text-ink-faint">
                  The error groups below were still produced by deterministic grouping.
                  Start a model to add hypotheses.
                </p>
              </div>
            )}
          </header>

          {/* --- Live progress / retry --------------------------------- */}
          <div className="mt-6">
            <AnalysisProgress
              report={report}
              liveSteps={liveSteps}
              running={running}
              onRetry={retry}
              retrying={retrying}
              error={error}
            />
          </div>

          {/* --- Prioritized groups ------------------------------------- */}
          <Section title="Prioritized error groups">
            {sortedGroups.length === 0 ? (
              <Empty
                title="No error groups found"
                body="Nothing in this input reached warning level. If you expected failures, check the detected format and try forcing one."
              />
            ) : (
              <ul className="divide-y divide-edge border-y border-edge">
                {sortedGroups.map((group) => (
                  <GroupRow
                    key={group.id}
                    group={group}
                    maxCount={maxCount}
                    onInspect={(lineNumber) => {
                      const match = report.evidence.find(
                        (e) => e.line_number === lineNumber,
                      );
                      if (match) setSelectedEvidenceId(match.id);
                    }}
                  />
                ))}
              </ul>
            )}
          </Section>

          {/* --- Findings ----------------------------------------------- */}
          <Section title="Findings">
            {report.findings.length === 0 ? (
              <Empty
                title="No findings"
                body={
                  report.ai_analysis_performed
                    ? "The model produced findings that did not survive validation."
                    : "No AI findings were produced for this run."
                }
              />
            ) : (
              <ul className="space-y-4">
                {report.findings.map((finding) => (
                  <FindingCard
                    key={finding.id}
                    finding={finding}
                    evidence={evidenceIndex}
                    onCite={(evidenceId) => setSelectedEvidenceId(evidenceId)}
                  />
                ))}
              </ul>
            )}
          </Section>

          {/* --- Warnings ------------------------------------------------ */}
          {(report.warnings.length > 0 || report.validation_errors.length > 0) && (
            <Section title="Processing notes">
              <ul className="space-y-2">
                {report.warnings.map((warning, i) => (
                  <li key={`w-${i}`} className="text-sm leading-relaxed text-ink-dim">
                    {warning}
                  </li>
                ))}
                {report.validation_errors.map((error, i) => (
                  <li
                    key={`v-${i}`}
                    className="text-sm leading-relaxed text-medium"
                  >
                    Rejected: {error}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>

        {/* --- Evidence drawer ----------------------------------------- */}
        {/* Two constraints the panel needs on wide screens:
            - a fixed width, or the flex child collapses to its content and the
              panel reads as a footnote under the report;
            - sticky, matching the placeholder. The drawer itself is
              `lg:static`, so without this the panel would render at the top of
              the column while the reader is scrolled past it - which looked
              like the drawer never opened at all.
            Also `lg:flex` + a capped height so the drawer's own scroll region
            is bounded instead of running off the bottom of the viewport. */}
        <div className="lg:sticky lg:top-20 lg:flex lg:max-h-[calc(100vh-6rem)] lg:w-[26rem] lg:shrink-0 lg:self-start lg:pt-6">
          {selectedEvidence ? (
            <EvidenceDrawer
              evidence={selectedEvidence}
              onClose={() => setSelectedEvidenceId(null)}
            />
          ) : selectedCitationMissing ? (
            <div className="rounded border border-medium/40 bg-panel px-5 py-6 text-center">
              <p className="text-sm font-medium text-medium">
                {selectedEvidenceId} is not in the evidence set
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
                The model cited a line that was not selected for display. Open the
                original log at that line to check the claim yourself.
              </p>
              <button
                type="button"
                onClick={() => setSelectedEvidenceId(null)}
                className="mt-3 rounded border border-edge px-2 py-1 text-xs text-ink-dim hover:text-ink"
              >
                Back to report
              </button>
            </div>
          ) : (
            <div className="hidden rounded border border-dashed border-edge px-5 py-10 text-center lg:block">
              <FileSearch className="mx-auto h-5 w-5 text-ink-faint" aria-hidden />
              <p className="mt-3 text-sm text-ink-dim">Evidence appears here.</p>
              <p className="mt-1 text-xs text-ink-faint">
                Select a citation or a line number to open the log lines behind a claim.
              </p>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <button
        type="button"
        onClick={() => router.push("/")}
        className="mb-6 text-xs text-ink-faint transition-colors hover:text-ink-dim"
      >
        ← All investigations
      </button>
      {children}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="mb-4 text-sm font-medium text-ink">{title}</h2>
      {children}
    </section>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded border border-dashed border-edge px-5 py-8 text-center">
      <FileSearch className="mx-auto h-5 w-5 text-ink-faint" aria-hidden />
      <p className="mt-3 text-sm text-ink">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-ink-faint">{body}</p>
    </div>
  );
}

function SummaryBar({ report }: { report: InvestigationReport }) {
  const s = report.summary;
  const stats = [
    s ? pluralise(s.total_lines, "line") : null,
    s ? pluralise(s.total_events, "event") : null,
    s ? pluralise(s.error_group_count, "error group") : null,
    s ? pluralise(s.warning_group_count, "warning group") : null,
    formatDuration(report.duration_seconds),
  ].filter(Boolean) as string[];

  return (
    <div className="mt-4 space-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-xs text-ink-faint">
        {stats.map((stat, i) => (
          <span key={stat} className="flex items-center gap-2">
            {i > 0 && <span className="text-edge-bright">·</span>}
            <span>{stat}</span>
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-faint">
        <span className="font-mono">{formatTime(report.created_at)}</span>
        {report.summary && (
          <span>
            format <span className="font-mono text-ink-dim">{report.summary.detected_runtime}</span>
          </span>
        )}
        {report.model_name && (
          <span>
            model{" "}
            <span className="font-mono text-ink-dim">
              {report.model_provider}/{report.model_name}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

function GroupRow({
  group,
  maxCount,
  onInspect,
}: {
  group: ErrorGroup;
  maxCount: number;
  onInspect: (lineNumber: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const color = severityColor(group.severity);

  return (
    <li className="sev-edge py-3 pl-4" style={{ "--sev-color": color } as React.CSSProperties}>
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          {open ? (
            <ChevronDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" aria-hidden />
          ) : (
            <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" aria-hidden />
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm text-ink">{group.title}</span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
              <span className="font-mono">{pluralise(group.count, "occurrence")}</span>
              <SeverityMark severity={group.severity} />
              {group.exception_type && (
                <span className="font-mono text-ink-dim">{group.exception_type}</span>
              )}
              {group.services.length > 0 && <span>{group.services.join(", ")}</span>}
            </span>
          </span>
        </button>
      </div>

      <div className="mt-2 pl-5">
        <SeverityBar severity={group.severity} count={group.count} max={maxCount} />
      </div>

      {open && (
        <div className="mt-3 space-y-3 pl-5">
          <p className="text-xs text-ink-dim">{group.severity_reason}</p>

          <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            <div className="flex gap-2">
              <dt className="text-ink-faint">First seen</dt>
              <dd className="font-mono text-ink-dim">{formatTime(group.first_seen)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-ink-faint">Last seen</dt>
              <dd className="font-mono text-ink-dim">{formatTime(group.last_seen)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-ink-faint">Fingerprint</dt>
              <dd className="font-mono text-ink-dim">{group.fingerprint}</dd>
            </div>
          </dl>

          {group.line_numbers.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs text-ink-faint">Occurrences on these lines</p>
              <div className="flex flex-wrap gap-1">
                {group.line_numbers.slice(0, 24).map((line) => (
                  <button
                    key={line}
                    type="button"
                    onClick={() => onInspect(line)}
                    className="rounded border border-edge px-1.5 py-0.5 font-mono text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
                  >
                    {line}
                  </button>
                ))}
                {group.line_numbers.length > 24 && (
                  <span className="px-1 py-0.5 font-mono text-[11px] text-ink-faint">
                    +{group.line_numbers.length - 24} more
                  </span>
                )}
              </div>
            </div>
          )}

          {group.representative_stack && (
            <details>
              <summary className="cursor-pointer text-xs text-ink-dim hover:text-ink">
                Representative stack trace
              </summary>
              <pre className="scroll-quiet mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-edge bg-panel px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-dim">
                {group.representative_stack}
              </pre>
            </details>
          )}
        </div>
      )}
    </li>
  );
}

function FindingCard({
  finding,
  evidence,
  onCite,
}: {
  finding: Finding;
  evidence: Map<string, string>;
  onCite: (evidenceId: string) => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const color = severityColor(finding.severity);

  return (
    <li className="sev-edge rounded border border-edge bg-panel py-4 pl-4 pr-4" style={{ "--sev-color": color } as React.CSSProperties}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="text-base font-medium text-ink">{finding.title}</h3>
        <div className="flex items-center gap-2">
          <SeverityMark severity={finding.severity} />
          <ConfidenceMark confidence={finding.confidence} />
        </div>
      </div>

      {finding.source === "deterministic" && (
        <p className="mt-2 inline-flex items-center gap-1.5 rounded border border-edge px-2 py-0.5 text-[11px] text-ink-faint">
          Derived from deterministic grouping, not the model
        </p>
      )}

      <p className="mt-3 text-sm leading-relaxed text-ink">
        <span className="text-ink-faint">Likely cause (a hypothesis, not a confirmed root cause): </span>
        {finding.likely_cause}
      </p>

      {finding.reasoning_summary && (
        <p className="mt-2 text-sm leading-relaxed text-ink-dim">{finding.reasoning_summary}</p>
      )}

      {/* Citations: the signature element. Each opens the real log line. */}
      {finding.evidence_ids.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowEvidence((v) => !v)}
            aria-expanded={showEvidence}
            className="text-xs text-ink-dim transition-colors hover:text-ink"
          >
            {showEvidence ? "Hide" : "Show"} {pluralise(finding.evidence_ids.length, "cited line")}
          </button>

          {showEvidence && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {finding.evidence_ids.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => onCite(id)}
                  className="rounded border border-edge-bright bg-base px-2 py-1 font-mono text-[11px] text-accent transition-colors hover:border-accent"
                >
                  {id}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {finding.alternative_hypotheses.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-ink-dim">Alternative hypotheses</p>
          <ul className="mt-1.5 space-y-1">
            {finding.alternative_hypotheses.map((item) => (
              <li key={item} className="text-sm leading-relaxed text-ink-dim">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {finding.missing_information.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-ink-dim">Missing or contradicting evidence</p>
          <ul className="mt-1.5 space-y-1">
            {finding.missing_information.map((item) => (
              <li key={item} className="text-sm leading-relaxed text-medium">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {finding.recommended_steps.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-ink-dim">Recommended next steps</p>
          <ol className="mt-2 space-y-3">
            {finding.recommended_steps.map((step, index) => (
              <li key={index} className="flex gap-3">
                <span className="mt-0.5 font-mono text-xs text-ink-faint">
                  {index + 1}.
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-relaxed text-ink">{step.action}</p>
                  {step.reason && (
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-dim">
                      Why: {step.reason}
                    </p>
                  )}
                  {step.verification && (
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">
                      Verify: {step.verification}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="mt-4 flex justify-end">
        <CopyButton
          value={findingToMarkdown(finding, evidence)}
          label="Copy this finding"
        />
      </div>
    </li>
  );
}

function ConfidenceMark({ confidence }: { confidence: Finding["confidence"] }) {
  const tone =
    confidence === "high"
      ? "text-medium"
      : confidence === "medium"
        ? "text-low"
        : "text-ink-faint";

  return (
    <span className={`rounded border border-edge px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>
      {confidence} confidence
    </span>
  );
}

/**
 * `useParams` suspends during prerender, so the view is wrapped in Suspense.
 */
export default function InvestigationPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-6xl px-6 py-10">
          <p className="flex items-center gap-2 text-sm text-ink-dim">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading report…
          </p>
        </div>
      }
    >
      <InvestigationView />
    </Suspense>
  );
}
