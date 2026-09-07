import { getJson, patchJson, postJson } from "./client";
import type {
  CancelRunResponse,
  CreateSessionResponse,
  ListSavedSessionsResponseDTO,
  RenameSessionResponseDTO,
  RewindSessionResponse,
  SessionHistoryResponseDTO,
} from "./types";

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

/** POST-5.1 B4C — the caller's own saved-chat list (`GET /api/sessions`).
 * Owner-scoped, already sorted newest genuine chat activity first, empty/
 * upload-only sessions already excluded — this frontend never re-derives
 * any of those rules itself. */
export async function listSavedSessions(): Promise<ListSavedSessionsResponseDTO> {
  return getJson<ListSavedSessionsResponseDTO>("/api/sessions");
}

/** POST-5.1 B4C — the safe, active-branch-only transcript for one saved
 * session (`GET /api/sessions/{id}/history`). Already final-assistant-only,
 * rewind-aware, and stripped of tool/state/internal events — never
 * second-guessed or re-filtered on this side. */
export async function getSessionHistory(sessionId: string): Promise<SessionHistoryResponseDTO> {
  return getJson<SessionHistoryResponseDTO>(`/api/sessions/${encodeURIComponent(sessionId)}/history`);
}

/** POST-5.1 B4C — durable manual rename for a real backend chat (`PATCH
 * /api/sessions/{id}`). Changes `chat_title` only; the backend leaves
 * `chat_activity_at`/sidebar ordering untouched, so no caller here needs to
 * separately re-sort anything after a successful rename. */
export async function renameSession(sessionId: string, title: string): Promise<RenameSessionResponseDTO> {
  return patchJson<RenameSessionResponseDTO>(`/api/sessions/${encodeURIComponent(sessionId)}`, { title });
}
