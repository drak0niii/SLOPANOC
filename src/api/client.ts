import { getApiBaseUrl } from "./config";

/** The backend's `SafeError.to_dict()` shape (backend/gateway/safe_error.py).
 * `reason` (Phase 4G) is optional and only present for a closed set of
 * denial cases (see backend/approval/schemas.py's `ApprovalDenialReason`)
 * — a structured, versioned classifier a caller can branch on directly,
 * never something to be inferred by matching `userMessage` prose. */
interface SafeErrorBody {
  errorCode: string;
  userMessage: string;
  retryable: boolean;
  correlationId: string;
  reason?: string;
}

function isSafeErrorBody(value: unknown): value is SafeErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.errorCode === "string" &&
    typeof record.userMessage === "string" &&
    typeof record.retryable === "boolean" &&
    typeof record.correlationId === "string" &&
    (record.reason === undefined || typeof record.reason === "string")
  );
}

/** Normalized API failure. When the backend's response body is the
 * standard SafeError shape, `message` is its real, already-safe
 * `userMessage` and `errorCode`/`retryable`/`correlationId`/`reason` are
 * populated; when it isn't (a non-JSON body, a network-level failure with
 * no response at all), `message` falls back to a generic string and
 * those fields stay `undefined`. Never carries raw response text/stack
 * traces — only ever the backend's own pre-written, safe message. */
export class ApiError extends Error {
  status: number;
  errorCode?: string;
  retryable?: boolean;
  correlationId?: string;
  /** Phase 4G — the structured `ApprovalDenialReason` value, when this
   * failure was one of a closed set of proposal-lifecycle denials
   * (expired/stale/already-consumed/etc). Never present for a generic
   * transport/gateway failure. */
  reason?: string;

  constructor(
    message: string,
    status: number,
    safe?: { errorCode?: string; retryable?: boolean; correlationId?: string; reason?: string },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = safe?.errorCode;
    this.retryable = safe?.retryable;
    this.correlationId = safe?.correlationId;
    this.reason = safe?.reason;
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${getApiBaseUrl()}${path}`, init);
}

/**
 * JSON POST helper. `body` is omitted entirely (no request body, no
 * `Content-Type` header) when `undefined` — the real backend's
 * `POST /api/sessions` endpoint declares no body parameter at all.
 */
export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    if (import.meta.env.DEV) {
      console.debug(`[api] ${path} failed with status ${response.status}`);
    }
    let safe: SafeErrorBody | null = null;
    try {
      const parsed = await response.json();
      if (isSafeErrorBody(parsed)) safe = parsed;
    } catch {
      // Not JSON, or not the SafeError shape — fall back to the generic
      // message below rather than guessing at what the body meant.
    }
    throw new ApiError(
      safe?.userMessage ?? "The request could not be completed. Please try again.",
      response.status,
      safe
        ? { errorCode: safe.errorCode, retryable: safe.retryable, correlationId: safe.correlationId, reason: safe.reason }
        : undefined,
    );
  }

  return (await response.json()) as T;
}
