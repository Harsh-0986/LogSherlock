"use client";

import { useEffect, useRef } from "react";
import type { Evidence } from "@/lib/types";
import { levelColor } from "@/lib/format";
import { X } from "lucide-react";

interface EvidenceDrawerProps {
  evidence: Evidence | null;
  onClose: () => void;
}

/**
 * The evidence drawer: the log lines behind a claim.
 *
 * This is the product's core promise made visible — every claim links back to
 * real log text. It docks beside the report on wide screens and overlays on
 * narrow ones.
 */
export function EvidenceDrawer({ evidence, onClose }: EvidenceDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!evidence) return;

    closeRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [evidence, onClose]);

  if (!evidence) return null;

  return (
    <>
      {/* Scrim: only on narrow screens, where the drawer overlays content. */}
      <div
        className="fixed inset-0 z-20 bg-base/70 backdrop-blur-[1px] lg:hidden"
        onClick={onClose}
        aria-hidden
      />

      <aside
        role="dialog"
        aria-modal="false"
        aria-label={`Evidence ${evidence.id}`}
        className="fixed inset-y-0 right-0 z-30 flex w-full max-w-xl flex-col border-l border-edge bg-panel lg:static lg:z-auto lg:w-[26rem] lg:max-w-none lg:shrink-0 lg:border-l"
      >
        <header className="flex items-start justify-between gap-4 border-b border-edge px-5 py-4">
          <div className="min-w-0">
            <p className="font-mono text-xs text-ink-faint">{evidence.id}</p>
            <p className="mt-0.5 truncate text-sm text-ink">
              Line {evidence.line_number}
              <span className="mx-2 text-edge-bright">/</span>
              <span style={{ color: levelColor(evidence.level) }}>{evidence.level}</span>
              <span className="mx-2 text-edge-bright">/</span>
              <span className="text-ink-dim">
                {evidence.kind === "context" ? "context" : "error event"}
              </span>
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close evidence"
            className="rounded border border-edge p-1 text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </header>

        <div className="scroll-quiet flex-1 overflow-y-auto px-5 py-4">
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink">
            {evidence.text}
          </pre>
        </div>

        <footer className="border-t border-edge px-5 py-3">
          <p className="text-xs text-ink-faint">
            Secrets in this line were redacted before it reached storage or the model.
          </p>
        </footer>
      </aside>
    </>
  );
}