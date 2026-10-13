import type { Severity } from "@/lib/types";
import { severityColor, severityLabel } from "@/lib/format";

interface SeverityMarkProps {
  severity: Severity;
  /** Render the word as well as the edge. */
  withLabel?: boolean;
  className?: string;
}

/**
 * Severity as a left edge plus an optional word.
 *
 * The edge carries the signal at a glance without adding a badge to compete
 * with the content. Colour is never the only cue — the label is always
 * available to assistive tech.
 */
export function SeverityMark({ severity, withLabel = true, className = "" }: SeverityMarkProps) {
  const color = severityColor(severity);

  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <span
        aria-hidden
        className="inline-block h-3 w-0.5 shrink-0 rounded-full"
        style={{ backgroundColor: color }}
      />
      {withLabel && (
        <span className="text-xs font-medium" style={{ color }}>
          {severityLabel(severity)}
        </span>
      )}
      <span className="sr-only">Severity: {severityLabel(severity)}</span>
    </span>
  );
}

/** A horizontal meter for how many occurrences a group accounts for. */
export function SeverityBar({
  severity,
  count,
  max,
}: {
  severity: Severity;
  count: number;
  max: number;
}) {
  const width = max > 0 ? Math.max(4, Math.round((count / max) * 100)) : 0;

  return (
    <div
      className="h-1 w-full overflow-hidden rounded-full bg-edge"
      role="img"
      aria-label={`${count} occurrences`}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${width}%`, backgroundColor: severityColor(severity) }}
      />
    </div>
  );
}