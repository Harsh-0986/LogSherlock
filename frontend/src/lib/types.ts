/**
 * Report types mirroring the backend contract (SPEC §10.6).
 *
 * Kept separate from `api.ts` (transport) so components depend on data shape,
 * not on how it is fetched.
 */

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Confidence = "low" | "medium" | "high";
export type LogLevel =
  | "TRACE"
  | "DEBUG"
  | "INFO"
  | "NOTICE"
  | "WARN"
  | "ERROR"
  | "FATAL"
  | "UNKNOWN";

export interface DeterministicSummary {
  total_lines: number;
  total_events: number;
  error_group_count: number;
  warning_group_count: number;
  runtime: string;
  detected_runtime: string;
}

export interface ErrorGroup {
  id: string;
  fingerprint: string;
  title: string;
  exception_type: string | null;
  severity: Severity;
  severity_reason: string;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
  services: string[];
  line_numbers: number[];
  representative_stack: string | null;
}

export interface RecommendedStep {
  action: string;
  reason: string;
  verification: string;
}

export interface Finding {
  id: string;
  fingerprint: string;
  title: string;
  severity: Severity;
  likely_cause: string;
  confidence: Confidence;
  evidence_ids: string[];
  reasoning_summary: string;
  alternative_hypotheses: string[];
  missing_information: string[];
  recommended_steps: RecommendedStep[];
  /** "deterministic" means the model did not produce this one. */
  source: "model" | "deterministic";
}

export interface Evidence {
  id: string;
  group_id: string | null;
  line_number: number;
  text: string;
  kind: "event" | "context";
  level: LogLevel;
}

export interface ProcessingStep {
  step: string;
  status: "running" | "completed" | "failed" | "skipped";
  started_at: string;
  completed_at: string | null;
  summary: string | null;
}

export interface InvestigationReport {
  id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;

  summary: DeterministicSummary | null;
  error_groups: ErrorGroup[];
  findings: Finding[];
  evidence: Evidence[];

  processing_steps: ProcessingStep[];
  warnings: string[];
  validation_errors: string[];
  error_message: string | null;

  model_provider: string | null;
  model_name: string | null;

  ai_skipped_reason: string | null;
  ai_analysis_performed: boolean;

  raw_input_included: boolean;
  raw_input: string | null;
}

/** Severity ranks, used for sorting. Higher is more urgent. */
export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  info: 1,
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};