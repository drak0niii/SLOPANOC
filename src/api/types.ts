/**
 * The frozen SLOPANOC backend public contract (Phase 4E), mirrored
 * field-for-field. Kept in wire/snake_case (not camelCased) so a future
 * phase (4G, real approval UI) can consume `PendingActionDTO` directly
 * without a translation layer.
 *
 * Source of truth: backend/api/streaming_events.py, backend/api/schemas.py.
 * Do not add fields here that the backend doesn't actually send.
 */

export interface SSEEventBase {
  session_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
}

export interface PendingActionDTO {
  proposal_id: string;
  operation: string;
  status: string;
  summary: string | null;
  title: string | null;
  members: string[];
  chat_id: string | null;
  message: string | null;
  expires_at: string;
  expires_in_seconds: number;
  expires_in_minutes: number;
  /** Presentation only — human-readable destination name (e.g. a Teams
   * chat's topic) for a teams.sendMessage proposal, mirroring the
   * backend's ActionProposal.target_display_name. Never the raw
   * chat_id; null when no authoritative name was available at proposal
   * time. Not populated for teams.createChat, which uses `title`
   * instead. */
  target_display_name: string | null;
}

export interface ActiveCaseDTO {
  case_id: string;
  title: string;
  status: string;
}

export interface RunStartedEvent extends SSEEventBase {
  type: "run.started";
  data: Record<string, never>;
}

export interface StatusEvent extends SSEEventBase {
  type: "status";
  data: { stage: string; label: string; presentation: "replace" };
}

export interface StatusClearEvent extends SSEEventBase {
  type: "status.clear";
  data: Record<string, never>;
}

export interface MessageDeltaEvent extends SSEEventBase {
  type: "message.delta";
  data: { text: string };
}

/** Pre-4H UX/provenance milestone — safe, structured provenance for a
 * Teams-derived answer. Rides `message.completed`'s own payload (never a
 * new event type — see backend/api/chat_service.py's own comment at the
 * call site) so it always arrives atomically with, and is owned by, the
 * exact message it describes. Mirrors backend/api/schemas.py's
 * `SourceReferenceDTO`/`SourceEvidenceItem` field-for-field — kept in
 * wire/snake_case, same rationale as `PendingActionDTO`. Never a raw
 * chat_id/message_id — see that DTO's own docstring. */
export interface SourceEvidenceItemDTO {
  author: string;
  sent_at: string;
  /** Snippet-authenticity fix — REQUIRED and always non-empty: the
   * backend never constructs a `SourceEvidenceItem` at all unless it
   * already found a valid, non-empty excerpt from the ACTUAL retrieved
   * Teams message (never from the model) — see backend/api/schemas.py's
   * `SourceEvidenceItem` docstring. The frontend still defensively
   * treats a malformed/empty value as "skip this item" (see
   * SourceChip.tsx's `hasDisplayableSnippet`) rather than trusting the
   * type alone. */
  snippet: string;
}

export interface SourceReferenceDTO {
  source_id: string;
  source_type: "teams";
  label: string;
  title: string | null;
  message_count: number | null;
  period_start: string | null;
  period_end: string | null;
  contributors: string[];
  evidence: SourceEvidenceItemDTO[];
}

/** Phase 5.1J correction pass (Part C) — safe, structured provenance for
 * a governed-knowledge-derived answer. Mirrors backend/api/schemas.py's
 * `KnowledgeSourceReferenceDTO` field-for-field. Deliberately excludes
 * `source_uri` — see docs/KNOWLEDGE_CONTRACT.md's Phase 5.1J section,
 * "DO NOT EXPOSE source_uri YET". A SEPARATE, additive shape from
 * `SourceReferenceDTO` (Teams) — never merged into one DTO, since the two
 * provenance kinds have genuinely different fields. */
export interface KnowledgeSourceReferenceDTO {
  source_id: string;
  source_type: "knowledge";
  label: string;
  knowledge_id: string;
  version_label: string;
  section_id: string;
  title: string;
  document_type: string;
  source_system: string;
  evidence_source_id: string;
  source_display_name: string | null;
  section_heading: string | null;
  source_locator: string | null;
  content: string;
}

export interface MessageCompletedEvent extends SSEEventBase {
  type: "message.completed";
  data: { content: string; source?: SourceReferenceDTO; knowledge_sources?: KnowledgeSourceReferenceDTO[] };
}

export interface ActionPendingEvent extends SSEEventBase {
  type: "action.pending";
  data: PendingActionDTO;
}

// --- Interaction-capability extension: interactive Teams chat-name
// resolution (mirrors the approval DTOs above field-for-field). ---------

export interface SelectionOptionDTO {
  option_id: string;
  label: string;
}

/** Never carries a raw Teams chat_id or the internal option_targets map
 * — `option_id` is an opaque, server-minted token; the frontend only
 * ever sends it back verbatim (see src/api/selections.ts). */
export interface PendingSelectionDTO {
  selection_id: string;
  kind: string;
  status: string;
  requested_value: string;
  options: SelectionOptionDTO[];
}

export interface SelectionPendingEvent extends SSEEventBase {
  type: "selection.pending";
  data: PendingSelectionDTO;
}

export interface ErrorEvent extends SSEEventBase {
  type: "error";
  data: { code: string; message: string };
}

export interface RunCompletedEvent extends SSEEventBase {
  type: "run.completed";
  data: { outcome: "ok" | "error" };
}

// --- Expandable, sanitized run trace (pre-4H milestone) --------------------

/** Small, closed allowlist of numeric counts a trace step may carry —
 * mirrors the backend's own allowlist (`run_trace.py`) field-for-field.
 * Never a raw id/url/name — see RunTrace.tsx's module docstring. */
export interface TraceStepSafeMetadata {
  message_count?: number;
  member_count?: number;
  evidence_count?: number;
  candidate_count?: number;
  document_count?: number;
}

export interface TraceStepDTO {
  step_id: string;
  category: string;
  label: string;
  status: "completed" | "warning" | "failed";
  safe_metadata?: TraceStepSafeMetadata;
}

export interface TraceStepEvent extends SSEEventBase {
  type: "trace.step";
  data: TraceStepDTO;
}

export type SSEEvent =
  | RunStartedEvent
  | StatusEvent
  | StatusClearEvent
  | MessageDeltaEvent
  | MessageCompletedEvent
  | ActionPendingEvent
  | SelectionPendingEvent
  | ErrorEvent
  | RunCompletedEvent
  | TraceStepEvent;

/** All valid `SSEEvent["type"]` values — used by the parser to reject
 * anything else instead of guessing. */
export const KNOWN_SSE_EVENT_TYPES = [
  "run.started",
  "status",
  "status.clear",
  "message.delta",
  "message.completed",
  "action.pending",
  "selection.pending",
  "error",
  "run.completed",
  "trace.step",
] as const;

export interface CreateSessionResponse {
  session_id: string;
}

/** Phase 4G hardening pass — conversational branching for editing a
 * historical user message. See `POST /api/sessions/{id}/rewind`'s
 * backend docstring (chat_service.py's `rewind_before_user_turn`) for
 * the full branching semantics; the frontend only ever needs to know
 * "which of my own already-active user turns to rewind before." */
export interface RewindSessionResponse {
  session_id: string;
}

/** Pre-4H refinement — response for `POST
 * /api/sessions/{id}/runs/{run_id}/cancel`. `cancelled` is `true` only
 * when the backend actually issued a real cancellation against a
 * genuinely still-running task; `false` covers every safe "nothing to
 * do" case (already finished, stale run_id, never existed) — never an
 * error. See backend/api/chat_service.py's `cancel_run` for exactly
 * what this can and cannot guarantee. */
export interface CancelRunResponse {
  session_id: string;
  run_id: string;
  cancelled: boolean;
}

/** The non-streaming endpoint's response shape. Not called anywhere in
 * Phase 4F (the streaming endpoint is used exclusively), but defined here
 * for completeness/future reuse — see the 4F report's documented
 * limitation on `active_case`, which only this shape carries. */
export interface ChatResponse {
  session_id: string;
  message: { role: "assistant"; content: string };
  pending_action: PendingActionDTO | null;
  active_case: ActiveCaseDTO | null;
}

// --- Phase 4G: approve/reject/execute ---------------------------------------

export interface ApprovalResponse {
  session_id: string;
  result: "approved" | "rejected";
  pending_action: PendingActionDTO | null;
}

/** Safe, minimal view of what Power Automate actually confirmed for a
 * just-executed action — 1:1 with the backend's `ExecutedActionDTO`.
 * Never fabricated: a field is `null` whenever the gateway's own response
 * didn't actually include it. */
export interface ExecutedActionDTO {
  chat_id: string | null;
  title: string | null;
  web_url: string | null;
}

/** Response for `POST /api/sessions/{id}/execute` — deliberately a
 * DIFFERENT shape from `ApprovalResponse`. A denial/failure never reaches
 * this type at all (it surfaces as a thrown `ApiError` instead, same
 * contract every other endpoint already uses) — `result` is only ever
 * the literal `"executed"`. Approving a proposal never implies this. */
export interface ExecuteActionResponse {
  session_id: string;
  result: "executed";
  pending_action: PendingActionDTO | null;
  executed_action: ExecutedActionDTO | null;
}

// --- Interaction-capability extension: choose/skip a Teams chat selection --

/** Response for `POST /api/sessions/{id}/selections/{selectionId}/choose`.
 * `pending_action` is set only when the resolved selection was for a
 * pending WRITE (teams.sendMessage) — the deterministic completion
 * already created a normal ActionProposal, ready for the existing
 * approve/execute flow. It stays `null` for a READ-kind selection.
 *
 * Hardening pass: `resume_message` (set only for a READ-kind selection,
 * `null` for a write) is the deterministic, destination-free text the
 * backend built from the selection's own stored read intent (see
 * backend/selection/read_resume.py) — the frontend passes this UNCHANGED
 * as the message for a brand-new backend turn to resume the read against
 * the now-authoritative selected chat. It is NEVER the user's own
 * original request text: that text still names the OLD, unresolved
 * destination, which previously caused the exact same ambiguity to
 * reopen instead of resolving. */
export interface ChooseSelectionResponse {
  session_id: string;
  selection_id: string;
  status: string;
  selected_label: string;
  pending_action: PendingActionDTO | null;
  resume_message: string | null;
}

export interface SkipSelectionResponse {
  session_id: string;
  selection_id: string;
  status: string;
}
