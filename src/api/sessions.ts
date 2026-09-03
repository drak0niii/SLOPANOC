import { postJson } from "./client";
import type { CancelRunResponse, CreateSessionResponse, RewindSessionResponse } from "./types";

/** Creates a new, server-authoritative ADK session. The backend's
 * `POST /api/sessions` route takes no request body — the client never
 * invents/supplies a session id. */
export async function createSession(): Promise<CreateSessionResponse> {
  return postJson<CreateSessionResponse>("/api/sessions");
}

/** Phase 4G hardening pass — editing a historical user message.
 * `beforeUserTurnIndex` is 0-based: how many of THIS chat's own
 * currently-active user messages precede the one being edited — a plain
 * count over the frontend's own structured message array, never a raw
 * backend event index or invocation id (see AppState.tsx's `editMessage`
 * for how it's computed). Reuses the SAME session id — a successful call
 * never changes `Chat.backendSessionId`; there is no new session to
 * switch onto. Throws (never partially applies) on failure — the caller
 * must not truncate the visible conversation unless this resolves. */
export async function rewindSession(
  sessionId: string,
  beforeUserTurnIndex: number,
): Promise<RewindSessionResponse> {
  return postJson<RewindSessionResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/rewind`, {
    before_user_turn_index: beforeUserTurnIndex,
  });
}

/** Pre-4H refinement — the Stop control's real, server-side cancellation.
 * `runId` is the backend's own `run_id`, already known to the frontend
 * from `run.started`'s event envelope (`ChatRunState.serverRunId`) — no
 * new identifier concept. Best-effort from this function's own point of
 * view: the caller (AppState.tsx's `stopActiveRun`) already stops the
 * frontend's own transport/UI synchronously before this resolves, so a
 * failure here never leaves the user stuck — see that function's own
 * docstring for why this call's outcome is never surfaced as a claim
 * about whether backend execution was truly interrupted. */
export async function cancelRun(sessionId: string, runId: string): Promise<CancelRunResponse> {
  return postJson<CancelRunResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`,
  );
}
