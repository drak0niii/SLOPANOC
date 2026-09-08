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

/** Shared by every request helper below — parses a non-OK response into
 * the standard SafeError shape when possible and throws `ApiError`.
 * Extracted (POST-5.1 B3) so `postJson` and `postForm` share EXACTLY one
 * error-mapping implementation rather than two copies drifting apart. */
async function throwForFailedResponse(path: string, response: Response): Promise<never> {
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

  if (!response.ok) return throwForFailedResponse(path, response);

  return (await response.json()) as T;
}

/**
 * Plain GET helper (POST-5.1 B4C). Shares `throwForFailedResponse` with
 * `postJson`/`postForm` — one error-mapping implementation for every verb.
 */
export async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path);
  if (!response.ok) return throwForFailedResponse(path, response);
  return (await response.json()) as T;
}

/**
 * Binary GET helper (POST-5.1 B4D) — currently only `GET
 * /api/attachments/{id}/content` (persisted chat-image rehydration).
 * Mirrors `getJson` exactly except for the final `.blob()` instead of
 * `.json()`, and forwards an optional `AbortSignal` so a caller (a
 * persisted-image component unmounting or re-targeting a new reference)
 * can cancel an in-flight fetch. Shares the exact same
 * `throwForFailedResponse` SafeError mapping as every other verb — a
 * failed/unauthorized/unknown attachment id never leaks a raw response
 * body, same contract as `postJson`/`getJson`/`patchJson`/`postForm`.
 */
export async function getBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await apiFetch(path, { signal });
  if (!response.ok) return throwForFailedResponse(path, response);
  return response.blob();
}

/**
 * JSON PATCH helper (POST-5.1 B4C) — currently only `PATCH
 * /api/sessions/{id}` (durable manual rename). Mirrors `postJson` exactly,
 * differing only in HTTP method.
 */
export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) return throwForFailedResponse(path, response);
  return (await response.json()) as T;
}

/**
 * `multipart/form-data` POST helper (POST-5.1 B3) — the chat attachment
 * upload endpoint's only current use. Deliberately never sets a
 * `Content-Type` header itself: the browser computes the correct
 * `multipart/form-data; boundary=...` value from the `FormData` body, and
 * setting it manually would omit/break that boundary. Shares
 * `throwForFailedResponse` with `postJson` — identical SafeError mapping,
 * one implementation. `signal`, when provided, lets a caller abort an
 * in-flight upload (see AppState.tsx's per-attachment `AbortController`).
 */
export async function postForm<T>(path: string, formData: FormData, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    body: formData,
    signal,
  });

  if (!response.ok) return throwForFailedResponse(path, response);

  return (await response.json()) as T;
}
