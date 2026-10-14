"use client";

import { Brain, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import type { InvestigationReport, ProcessingStep } from "@/lib/types";
import { StepProgress } from "@/components/StepProgress";

/** Human label for the stage the backend is currently in. */
function stageLabel(step: string | null): string | null {
  switch (step) {
    case "validate_input":
      return "Checking the input";
    case "parse_logs":
      return "Reading the logs";
    case "normalize_and_group":
      return "Grouping recurring errors";
    case "rank_errors":
      return "Ranking by severity";
    case "select_evidence":
      return "Selecting evidence";
    case "investigate_with_model":
      return "Analysing with the model — this is the slow step";
    case "validate_findings":
      return "Checking every citation";
    case "bounded_repair":
      return "Correcting one rejected answer";
    case "persist_report":
      return "Saving the report";
    default:
      return null;
  }
}

interface Props {
  report: InvestigationReport;
  liveSteps: ProcessingStep[];
  running: boolean;
  onRetry: () => void;
  retrying: boolean;
  error: string | null;
}

/**
 * Live progress panel.
 *
 * Shown whenever a run is in flight, and whenever AI analysis was skipped or
 * produced nothing — the two moments a user most needs an explanation and a
 * way forward. The retry button re-runs only the model stage; the stored
 * groups and evidence are reused, so it costs far less than the first run.
 */
export function AnalysisProgress({ report, liveSteps, running, onRetry, retrying, error }: Props) {
  const terminal = ["completed", "partial", "failed"].includes(report.status);
  const steps = running ? liveSteps.length > 0 ? liveSteps : report.processing_steps : report.processing_steps;
  const label = stageLabel(running ? (liveSteps.at(-1)?.step ?? null) : null);

  if (!running && terminal && report.ai_analysis_performed) {
    return null;
  }

  return (
    <section
      className="rounded border border-edge bg-panel px-5 py-4"
      aria-live="polite"
      aria-busy={running}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          {running ? (
            <Brain className="mt-0.5 h-4 w-4 shrink-0 animate-pulse text-accent" aria-hidden />
          ) : (
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-medium" aria-hidden />
          )}
          <div>
            <p className="text-sm font-medium text-ink">
              {running ? (label ?? "Working") : "No AI analysis in this report"}
            </p>
            <p className="mt-1 max-w-xl text-sm leading-relaxed text-ink-dim">
              {running ? (
                <>
                  The model is called once per error group, a few seconds apart. The
                  deterministic grouping below is already finished and unaffected.
                </>
              ) : (
                report.ai_skipped_reason ??
                "The model produced no answer that passed validation."
              )}
            </p>
          </div>
        </div>

        {!running && report.can_retry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="inline-flex shrink-0 items-center gap-2 rounded border border-edge-bright px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink-faint disabled:opacity-50"
          >
            {retrying ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
            {retrying ? "Retrying…" : "Retry AI analysis"}
          </button>
        )}
      </div>

      {report.retry_count > 0 && !running && (
        <p className="mt-2 text-xs text-ink-faint">
          Retried {report.retry_count} {report.retry_count === 1 ? "time" : "times"}.
        </p>
      )}

      {error && <p className="mt-2 text-sm text-critical">{error}</p>}

      {steps.length > 0 && (
        <div className="mt-4 border-t border-edge pt-3">
          <StepProgress steps={steps} pending={running} />
        </div>
      )}
    </section>
  );
}
