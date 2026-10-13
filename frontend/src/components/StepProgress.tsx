"use client";

import type { ProcessingStep } from "@/lib/types";
import { Check, Loader2, MinusCircle, XCircle } from "lucide-react";

interface StepProgressProps {
  steps: ProcessingStep[];
  /** Shown while waiting for the first step to land. */
  pending?: boolean;
}

/**
 * Real processing steps, in execution order (SPEC §6.5, §8.4).
 *
 * Numbering is warranted here because the graph genuinely is a sequence. Each
 * step shows what it actually did, so a failure is legible rather than opaque.
 */
export function StepProgress({ steps, pending = false }: StepProgressProps) {
  if (steps.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-ink-dim">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {pending ? "Starting investigation…" : "Waiting for the first step…"}
      </p>
    );
  }

  return (
    <ol className="space-y-0">
      {steps.map((step, index) => (
        <li key={`${step.step}-${index}`} className="flex gap-3 py-2">
          <div className="flex flex-col items-center">
            <StatusIcon status={step.status} />
            {index < steps.length - 1 && (
              <span className="mt-1 w-px flex-1 bg-edge" aria-hidden />
            )}
          </div>

          <div className="min-w-0 flex-1 pb-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-xs text-ink-dim">{step.step}</span>
              {step.status === "skipped" && (
                <span className="text-[11px] text-ink-faint">skipped</span>
              )}
            </div>
            {step.summary && (
              <p className="mt-0.5 text-sm leading-snug text-ink">{step.summary}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function StatusIcon({ status }: { status: ProcessingStep["status"] }) {
  switch (status) {
    case "completed":
      return (
        <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-edge-bright">
          <Check className="h-2.5 w-2.5 text-accent" aria-hidden />
        </span>
      );
    case "running":
      return (
        <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-edge-bright">
          <Loader2 className="h-2.5 w-2.5 animate-spin text-ink-dim" aria-hidden />
        </span>
      );
    case "failed":
      return (
        <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-critical/50">
          <XCircle className="h-2.5 w-2.5 text-critical" aria-hidden />
        </span>
      );
    default:
      return (
        <span className="mt-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-edge">
          <MinusCircle className="h-2.5 w-2.5 text-ink-faint" aria-hidden />
        </span>
      );
  }
}