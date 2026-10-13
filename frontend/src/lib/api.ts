/**
 * Typed client for the LogSherlock backend.
 *
 * The frontend never talks to the model runtime directly (SPEC §5.2) — every
 * call goes through the FastAPI backend at NEXT_PUBLIC_API_BASE_URL.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

/** SPEC §10.9 error envelope. Every non-2xx response uses this shape. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

/** SPEC §10.2 */
export interface ModelStatus {
  provider: string;
  model: string;
  available: boolean;
  detail?: string;
}

/** SPEC §10.3 */
export interface StartInvestigationResponse {
  id: string;
  status: InvestigationStatus;
}

export type InvestigationStatus =
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed";

/** SPEC §10.5. Re-exported from the shared report types so there is one source. */
export type { ProcessingStep } from "./types";
import type { ProcessingStep } from "./types";

export interface InvestigationStatusResponse {
  id: string;
  status: InvestigationStatus;
  current_step: string | null;
  steps: ProcessingStep[];
  error_message?: string | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = "HTTP_ERROR";
  let message = `Request failed with status ${response.status}`;
  let details: Record<string, unknown> = {};

  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? {};
    }
  } catch {
    // Non-JSON body (e.g. a proxy error page) — keep the generic message.
  }

  return new ApiError(code, message, response.status, details);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        // FormData must keep the browser's own multipart Content-Type.
        ...(init?.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init?.headers,
      },
    });
  } catch {
    // Network-level failure: backend down, CORS blocked, wrong base URL.
    throw new ApiError(
      "NETWORK_ERROR",
      "Could not reach the LogSherlock backend. Is it running?",
      0,
    );
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  modelStatus: () => request<ModelStatus>("/api/v1/model/status"),

  startInvestigation: (body: {
    content: string;
    filename?: string | null;
    runtime?: string;
    context?: string | null;
  }) =>
    request<StartInvestigationResponse>("/api/v1/investigations", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  uploadInvestigation: (
    file: File,
    options: { runtime?: string; context?: string | null } = {},
  ) => {
    // FormData sets its own Content-Type with the multipart boundary; the
    // shared `request` helper must not overwrite it.
    const form = new FormData();
    form.append("file", file);
    form.append("runtime", options.runtime ?? "auto");
    if (options.context) form.append("context", options.context);

    return request<StartInvestigationResponse>("/api/v1/investigations/upload", {
      method: "POST",
      body: form,
    });
  },

  investigationStatus: (id: string) =>
    request<InvestigationStatusResponse>(`/api/v1/investigations/${id}/status`),

  investigationReport: <T>(id: string) =>
    request<T>(`/api/v1/investigations/${id}`),

  listInvestigations: (params: { limit?: number; offset?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    const qs = search.toString();
    return request<{ items: InvestigationListItem[]; total: number; limit: number; offset: number }>(
      `/api/v1/investigations${qs ? `?${qs}` : ""}`,
    );
  },

  deleteInvestigation: (id: string) =>
    request<void>(`/api/v1/investigations/${id}`, { method: "DELETE" }),
};

export interface InvestigationListItem {
  id: string;
  status: InvestigationStatus;
  runtime: string | null;
  summary: string | null;
  error_group_count: number;
  event_count: number;
  model_provider: string | null;
  model_name: string | null;
  created_at: string;
  completed_at: string | null;
}