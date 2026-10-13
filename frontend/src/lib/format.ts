/** Presentation helpers: severity colours, formatting, and Markdown export. */

import type { Finding, InvestigationReport, Severity } from "./types";
import { SEVERITY_LABEL } from "./types";

/** Map a severity onto its design token. */
export function severityColor(severity: Severity): string {
  switch (severity) {
    case "critical":
      return "var(--color-critical)";
    case "high":
      return "var(--color-high)";
    case "medium":
      return "var(--color-medium)";
    case "low":
      return "var(--color-low)";
    default:
      return "var(--color-info)";
  }
}

/** Human-readable severity name. */
export function severityLabel(severity: Severity): string {
  return SEVERITY_LABEL[severity];
}

/** Colour for a log level token. */
export function levelColor(level: string): string {
  switch (level) {
    case "FATAL":
      return "var(--color-critical)";
    case "ERROR":
      return "var(--color-high)";
    case "WARN":
      return "var(--color-medium)";
    case "INFO":
    case "NOTICE":
      return "var(--color-low)";
    default:
      return "var(--color-ink-faint)";
  }
}

/** Format an ISO timestamp for display, tolerating null. */
export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Format a duration in seconds. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
}

/** Pluralise a count: `pluralise(1, "group")` -> "1 group". */
export function pluralise(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}

/** Group evidence by its id for O(1) lookup when rendering citations. */
export function indexEvidence(report: InvestigationReport): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of report.evidence) {
    map.set(item.id, item.text);
  }
  return map;
}

export interface MarkdownOptions {
  /** Include the raw evidence block at the end. */
  includeEvidence?: boolean;
}

/**
 * Render a report as Markdown for the clipboard (SPEC §6.6).
 *
 * Keeps the evidence-before-explanation ordering so a pasted report still
 * reads as a case file.
 */
export function reportToMarkdown(
  report: InvestigationReport,
  options: MarkdownOptions = {},
): string {
  const { includeEvidence = true } = options;
  const lines: string[] = [];

  lines.push(`# LogSherlock investigation ${report.id}`);
  lines.push("");
  lines.push(`Status: **${report.status}**`);

  if (report.summary) {
    lines.push(
      `Lines: ${report.summary.total_lines} · Events: ${report.summary.total_events} · ` +
        `Error groups: ${report.summary.error_group_count} · ` +
        `Warning groups: ${report.summary.warning_group_count}`,
    );
    lines.push(`Detected format: ${report.summary.detected_runtime}`);
  }

  if (report.model_name) {
    lines.push(`Model: ${report.model_provider ?? "unknown"}/${report.model_name}`);
  }
  if (report.duration_seconds !== null) {
    lines.push(`Duration: ${formatDuration(report.duration_seconds)}`);
  }

  if (!report.ai_analysis_performed && report.ai_skipped_reason) {
    lines.push("");
    lines.push(
      `> **No AI analysis.** ${report.ai_skipped_reason} The groups below were ` +
        "produced by deterministic grouping only.",
    );
  }

  // --- Prioritized error groups ---------------------------------------
  if (report.error_groups.length > 0) {
    lines.push("");
    lines.push("## Prioritized error groups");
    lines.push("");
    for (const group of report.error_groups) {
      lines.push(
        `### ${group.title}`,
        "",
        `- Severity: **${severityLabel(group.severity)}** — ${group.severity_reason}`,
        `- Occurrences: ${group.count}`,
      );
      if (group.exception_type) lines.push(`- Exception: \`${group.exception_type}\``);
      if (group.services.length > 0) lines.push(`- Services: ${group.services.join(", ")}`);
      if (group.first_seen && group.last_seen) {
        lines.push(`- First seen: ${formatTime(group.first_seen)}`);
        lines.push(`- Last seen: ${formatTime(group.last_seen)}`);
      }
      lines.push(`- Fingerprint: \`${group.fingerprint}\``);
      if (group.representative_stack) {
        lines.push("", "```", group.representative_stack, "```");
      }
      lines.push("");
    }
  }

  // --- Findings ---------------------------------------------------------
  if (report.findings.length > 0) {
    lines.push("## Findings");
    lines.push("");
    for (const finding of report.findings) {
      lines.push(
        `### ${finding.title}`,
        "",
        `**Likely cause (hypothesis, not confirmed):** ${finding.likely_cause}`,
        "",
        `Confidence: **${finding.confidence}** · Severity: ${severityLabel(finding.severity)}`,
      );

      if (finding.source === "deterministic") {
        lines.push("", "> Derived from deterministic grouping, not from the model.");
      }

      if (finding.reasoning_summary) {
        lines.push("", finding.reasoning_summary);
      }

      if (finding.evidence_ids.length > 0) {
        lines.push("", `Evidence: ${finding.evidence_ids.map((id) => `\`${id}\``).join(", ")}`);
      }

      if (finding.alternative_hypotheses.length > 0) {
        lines.push("", "**Alternative hypotheses**");
        for (const item of finding.alternative_hypotheses) lines.push(`- ${item}`);
      }

      if (finding.missing_information.length > 0) {
        lines.push("", "**Missing or contradicting evidence**");
        for (const item of finding.missing_information) lines.push(`- ${item}`);
      }

      if (finding.recommended_steps.length > 0) {
        lines.push("", "**Recommended next steps**");
        finding.recommended_steps.forEach((step, index) => {
          lines.push(`${index + 1}. ${step.action}`);
          if (step.reason) lines.push(`   - Why: ${step.reason}`);
          if (step.verification) lines.push(`   - Verify: ${step.verification}`);
        });
      }

      lines.push("");
    }
  }

  // --- Raw evidence ------------------------------------------------------
  if (includeEvidence && report.evidence.length > 0) {
    lines.push("## Cited evidence");
    lines.push("");
    for (const item of report.evidence) {
      lines.push(`**${item.id}** (line ${item.line_number}, ${item.level})`, "", "```", item.text, "```", "");
    }
  }

  if (report.warnings.length > 0) {
    lines.push("## Processing warnings", "");
    for (const warning of report.warnings) lines.push(`- ${warning}`);
    lines.push("");
  }

  return lines.join("\n");
}

/** Render a single finding as Markdown, for the per-finding copy action. */
export function findingToMarkdown(finding: Finding, evidence: Map<string, string>): string {
  const lines: string[] = [
    `### ${finding.title}`,
    "",
    `**Likely cause (hypothesis, not confirmed):** ${finding.likely_cause}`,
    "",
    `Confidence: **${finding.confidence}** · Severity: ${severityLabel(finding.severity)}`,
  ];

  if (finding.source === "deterministic") {
    lines.push("", "> Derived from deterministic grouping, not from the model.");
  }
  if (finding.reasoning_summary) lines.push("", finding.reasoning_summary);

  if (finding.evidence_ids.length > 0) {
    lines.push("", "**Evidence**", "");
    for (const id of finding.evidence_ids) {
      const text = evidence.get(id);
      lines.push(`\`${id}\`:`, "", "```", text ?? "(not available)", "```", "");
    }
  }

  if (finding.alternative_hypotheses.length > 0) {
    lines.push("**Alternative hypotheses**");
    for (const item of finding.alternative_hypotheses) lines.push(`- ${item}`);
    lines.push("");
  }

  if (finding.missing_information.length > 0) {
    lines.push("**Missing or contradicting evidence**");
    for (const item of finding.missing_information) lines.push(`- ${item}`);
    lines.push("");
  }

  if (finding.recommended_steps.length > 0) {
    lines.push("**Recommended next steps**", "");
    finding.recommended_steps.forEach((step, index) => {
      lines.push(`${index + 1}. ${step.action}`);
      if (step.reason) lines.push(`   - Why: ${step.reason}`);
      if (step.verification) lines.push(`   - Verify: ${step.verification}`);
    });
    lines.push("");
  }

  return lines.join("\n");
}