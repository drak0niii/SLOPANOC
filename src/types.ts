import type {
  KnowledgeSourceReferenceDTO,
  PendingActionDTO,
  PendingSelectionDTO,
  SourceReferenceDTO,
  TraceStepSafeMetadata,
} from "./api/types";

/** Stops on one axis, ordered from fastest to most thorough. The slider
 * derives its geometry from this order, so adding a stop is a one-line change
 * here plus a label. */
export type ThinkingEffort = "instant" | "advanced";

export interface Skill {
  id: string;
  name: string;
  description: string;
  instructions: string;
}

export interface AiModel {
  id: string;
  name: string;
}

export type ConnectorState =
  | "connected"
  | "not_connected"
  | "permission_required"
  | "error";

export interface Connector {
  id: string;
  name: string;
  purpose: string;
  state: ConnectorState;
  capability: "read" | "read_write";
}

export type AttachmentKind = "file" | "folder";

export interface Attachment {
  id: string;
  kind: AttachmentKind;
  name: string;
  meta?: string;
  /** Marks the mock "Pasted text.txt" attachment created when a message's
   * typed/pasted text exceeds the long-paste threshold — lets the message
   * renderer hide the raw inline text and offer "Expand message" instead. */
  isPastedText?: boolean;
  /** The full pasted text, present only on `isPastedText` attachments —
   * carries the content from the moment of paste (before it's ever in
   * `draft.text`) through to the sent message. */
  content?: string;
}

/** POST-5.1 B3 — the browser-only lifecycle of one durable-backed image
 * attachment while it's still a draft. `PENDING` briefly, before upload
 * starts; `UPLOADING` while the real POST is in flight; `READY` once the
 * backend has returned a real `attachment_id`; `FAILED` on any rejection
 * (validation, network, backend error). This state is presentation-only —
 * it is never persisted to Cloud SQL (the backend's own `ChatAttachment
 * .status` is a completely separate, server-owned concept: READY/LINKED/
 * DELETED — see backend/attachments/models.py). */
export type DraftImageUploadState = "pending" | "uploading" | "ready" | "failed";

/** POST-5.1 B3 — a real image selected/pasted into the composer, backed by
 * an actual `File`, not yet (or not successfully) part of any sent
 * message. `file`/`objectUrl` are BROWSER-ONLY — they must never be read
 * by anything that assumes `Draft`/`Message` state is serializable (there
 * is no `JSON.stringify` path in this app today, but keep it that way).
 * `id` is client-minted (see `createId`) and stable for this draft slot's
 * whole lifetime, independent of `attachmentId` (the durable backend id,
 * set only once upload succeeds) — this is what `removeAttachment`/retry/
 * the object-URL-cleanup effect key off of, uniformly with the existing
 * `Attachment.id` field (see `DraftAttachment` below). */
export interface DraftImageAttachment {
  kind: "image";
  id: string;
  file: File;
  /** `URL.createObjectURL(file)` — local preview only, revoked once this
   * entry leaves the draft (see AppState.tsx's object-URL cleanup effect).
   * Never a base64/data URL. */
  objectUrl: string;
  uploadState: DraftImageUploadState;
  /** The durable backend id — set only once `uploadState === "ready"`. */
  attachmentId?: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  /** Safe, already user-facing text — set only when `uploadState ===
   * "failed"`. Never raw backend/network exception text. */
  error?: string;
}

/** POST-5.1 B3 — everything `Draft.attachments` may hold: the existing
 * metadata-only `Attachment` (still the ONLY shape long-paste "Pasted
 * text.txt" entries ever use — that subsystem is deliberately untouched,
 * see PromptComposer.tsx's `handlePaste`) union'd with the new real-image
 * draft shape. Discriminated on `kind` — `Attachment.kind` is `"file" |
 * "folder"`, `DraftImageAttachment.kind` is `"image"`, so the two never
 * collide and TypeScript narrows correctly on a plain `kind` check. This
 * is the smallest safe union (instruction: do not rewrite the long-paste
 * subsystem to unify it with the new image type). */
export type DraftAttachment = Attachment | DraftImageAttachment;

/** POST-5.1 B3 — prepared for B4/B5, NOT produced by anything yet: the
 * durable, backend-linked shape a SENT message's attachment will
 * eventually use once B5 actually links a `READY` attachment to a real
 * user message. Deliberately minimal (mirrors the backend's own
 * `AttachmentResponse` DTO — see backend/api/schemas.py) — no `File`, no
 * `objectUrl`, nothing browser-only. Nothing in this codebase constructs
 * one yet; `Message.attachments` still uses the plain `Attachment` type
 * (correct for now: a real image can never reach a sent message in B3,
 * since Send is gated closed while one is in the draft — see AppState
 * .tsx's `hasBlockingImageAttachment`). */
export interface PersistedAttachmentReference {
  attachmentId: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
}

export type CitationScope = "global" | "project";

export interface Citation {
  id: string;
  docId: string;
  docTitle: string;
  version: string;
  status: "approved";
  section: string;
  excerpt: string;
  scope: CitationScope;
  scopeLabel: string;
}

export type MessageStatus = "pending" | "streaming" | "complete" | "error";

export type GroundingResult = "insufficient";

export interface ActionProposalField {
  label: string;
  value: string;
}

export type ActionProposalStatus =
  | "pending"
  | "approved"
  | "processing"
  | "cancelled"
  | "completed";

export interface ActionProposal {
  id: string;
  chatId: string;
  connectorId: string;
  actionType: string;
  title: string;
  fields: ActionProposalField[];
  body: string;
  status: ActionProposalStatus;
  /** Present only for `actionType === "schedule_task"` — the data actually
   * used to create the ScheduledTask once the proposal is approved. */
  scheduledTaskDraft?: {
    name: string;
    instructions: string;
    frequency: ScheduledTaskFrequency;
    timeOfDay: string;
    sources: TaskSource[];
  };
  /** Set once the proposal is approved and a real ScheduledTask is created —
   * lets the UI link straight to it instead of guessing by name/instructions. */
  createdTaskId?: string;
}

export type ConnectorUnavailableReason = "global_unavailable" | "project_disabled";

export interface ConnectorUnavailableInfo {
  connectorId: string;
  connectorName: string;
  reason: ConnectorUnavailableReason;
}

export interface Message {
  id: string;
  chatId: string;
  role: "user" | "assistant";
  text: string;
  status: MessageStatus;
  citations?: Citation[];
  attachments?: Attachment[];
  groundingResult?: GroundingResult;
  searchedScope?: string[];
  actionProposalId?: string;
  connectorUnavailable?: ConnectorUnavailableInfo;
  /** Connectors surfaced as "could help with this" — shown as a mock
   * connect-prompt card above any action proposal on the same message. */
  suggestedConnectorIds?: string[];
  /** A "send this response to…" proposal raised from the message action row.
   * Kept separate from `actionProposalId` so sharing never clobbers a
   * proposal the assistant already attached to the same message. */
  distributionProposalId?: string;
  /** Ad-hoc chat-room / calendar / inbox endpoints attached to a user
   * message via the composer's "+" menu. */
  sources?: TaskSource[];
  createdAt: number;
  /** Optional speaker name shown above the message. Not used by current mock content. */
  speakerLabel?: string;
  /** Present only when status === "error" (a real backend run failure, or
   * a transport/network failure before any backend error event existed).
   * Already sanitized/safe to render directly — falls back to a generic
   * string in the renderer if absent. */
  errorMessage?: string;
}

/** Identifies which deterministic executive-demo script a chat is following. */
export type DemoScenarioId = "ru-advise" | "ru-refine" | "ru-resolve" | "schedule-setup";

/**
 * Runtime progress through a demo script. Set only when a chat's opening
 * prompt matches one of the deterministic demo triggers — never seeded.
 */
export interface DemoRun {
  scenarioId: DemoScenarioId;
  /** Index of the next scripted assistant turn to play. */
  step: number;
}

export interface Chat {
  id: string;
  title: string;
  projectId: string | null;
  pinned: boolean;
  pinnedAt?: number;
  /** Mock "unread" flag toggled from the chat's menu — cleared automatically
   * when the chat is opened, same as any normal inbox. */
  unread?: boolean;
  createdAt: number;
  messageIds: string[];
  activeSkillId: string | null;
  connectorIds: string[];
  selectedModelId: string;
  thinkingEffort: ThinkingEffort;
  /** Present only while this chat is following a deterministic demo script. */
  demoRun?: DemoRun;
  /** Set when this chat is owned by a scheduled task. Owned chats are listed
   * under Tasks in the sidebar, never under Chats — one task, one row. */
  scheduledTaskId?: string;
  /** Phase 4F — set once, the first time a real backend send happens for
   * this chat (general workspace scope only). Never restored from
   * history/localStorage; a brand-new chat always gets a brand-new
   * backend session on its own first send, and Project-scoped chats never
   * set this at all (they stay on the mock system). */
  backendSessionId?: string;
  /** Phase 4F — present ONLY while a real backend run is in flight for
   * this chat; its mere presence IS the "is this chat currently running a
   * backend turn" signal. Cleared (undefined) on run.completed or a fatal
   * transport error. */
  run?: ChatRunState;
  /** Phase 4F — the most recent action.pending event's DTO, if any.
   * Durable across run.completed (a pending action is NOT cleared just
   * because a new run starts) — only ever replaced by a newer
   * action.pending event. Phase 4G hardening: this remains useful as
   * *authoritative current backend state* (which proposal, if any, is
   * presently approvable) — used to gate whether an individual
   * `actionCards` entry is still actionable — but it is deliberately NOT
   * used for transcript placement/rendering. See `actionCards` below. */
  pendingAction?: PendingActionDTO | null;
  /** Phase 4G — which message currently owns the chat's live
   * `pendingAction` (i.e. `actionCards[pendingActionMessageId].proposalId
   * === pendingAction.proposal_id`). Reset together with `pendingAction`
   * on a genuinely new proposal. Not used for rendering (see
   * `actionCards`) — only to resolve which card an approve/reject success
   * response should update. */
  pendingActionMessageId?: string;
  /** Phase 4G hardening pass — one action-card record per assistant
   * message that ever emitted an action.pending event, keyed by that
   * message's id. This is what transcript rendering reads from: an
   * action card belongs permanently to the message/turn that created it
   * and must never move, even after newer turns/proposals arrive. Each
   * record freezes its own `pendingAction` snapshot and lifecycle
   * (`approvalCard`) state; only the record whose `proposalId` still
   * matches `pendingAction.proposal_id` is live/mutable going forward —
   * older records become read-only history once superseded. See
   * src/lib/approvalCard.ts's `deriveApprovalCardView`. */
  actionCards?: Record<string, ActionCardRecord>;
  /** Interaction-capability extension — the most recent selection.pending
   * event's DTO, if any, mirroring `pendingAction` exactly. Cleared
   * (unlike `pendingAction`) whenever a later run completes without
   * re-affirming this SAME selection — see BACKEND_RUN_COMPLETED in
   * AppState.tsx. A Teams disambiguation is only meaningful to act on
   * while the conversation hasn't already moved past it (e.g. via an
   * exact-match resolution elsewhere — backend supersedes it
   * server-side too, see supersede_active_selection). */
  pendingSelection?: PendingSelectionDTO | null;
  /** Which message currently owns `pendingSelection` — mirrors
   * `pendingActionMessageId`. */
  pendingSelectionMessageId?: string;
  /** Which run (`ChatRunState.runToken`) most recently affirmed
   * `pendingSelection` — used only to detect "a later run completed
   * without re-affirming this selection," never rendered. */
  pendingSelectionRunToken?: string;
  /** One selection-card record per assistant message that ever emitted
   * a selection.pending event, keyed by that message's id — mirrors
   * `actionCards` exactly (one selection_id = one card = one originating
   * message; never moves; never duplicates). */
  selectionCards?: Record<string, SelectionCardRecord>;
  /** Expandable, sanitized run trace (pre-4H milestone) — one record per
   * assistant message that ever owned a real backend run, keyed by that
   * message's id. Mirrors `actionCards`/`selectionCards`'s ownership
   * model exactly: one run_id = one trace = one originating message,
   * permanently — never moves to a later message, never duplicated, and
   * a user's own expand/collapse choice on a historical trace is never
   * reset by a later turn (see RunTrace.tsx). */
  runTraces?: Record<string, RunTraceRecord>;
  /** Pre-4H UX/provenance milestone — one structured Teams source
   * reference per assistant message that ever produced grounded
   * evidence, keyed by that message's id. Mirrors `actionCards`/
   * `selectionCards`/`runTraces`'s exact same ownership model: one
   * source belongs permanently to the message/turn that produced it,
   * never moves to a later message, never duplicated. Kept in the
   * backend's own wire/snake_case shape (`SourceReferenceDTO`), same
   * rationale as `PendingActionDTO` — no translation layer needed. */
  sources?: Record<string, SourceReferenceDTO>;
  /** Phase 5.1J correction pass (Part C) — governed-knowledge source
   * references, keyed by the owning assistant message's id, mirroring
   * `sources` above's exact same message-ownership model (never moves,
   * never duplicated, disappears if the owning message is discarded by
   * edit/rewind). A SEPARATE field from `sources` (never merged into it)
   * since Teams and KM provenance are structurally different shapes — a
   * combined-answer message may legitimately have entries in BOTH.
   * Zero or more entries per message (a model may select multiple
   * distinct governed sections). */
  knowledgeSources?: Record<string, KnowledgeSourceReferenceDTO[]>;
  /** POST-5.1 B4C — set ONLY for a chat backed by a real backend session
   * whose transcript may still need fetching: `"unloaded"` (a saved-chat
   * summary was hydrated at boot, but `GET /api/sessions/{id}/history` has
   * never been called), `"loading"` (that call is in flight), `"loaded"`
   * (the transcript above is authoritative), `"error"` (the last attempt
   * failed — retryable, see `historyError`). Left `undefined` for every
   * other chat (local/mock/demo/project, and any backend chat that already
   * owns its own live, freshly-sent transcript) — those never attempt a
   * history fetch at all. Never `messageIds.length === 0` alone as the
   * signal: a real history can legitimately be a single failed user-only
   * turn with a still-empty-looking transcript otherwise. */
  historyHydrationStatus?: "unloaded" | "loading" | "loaded" | "error";
  /** Safe, already user-facing message for `historyHydrationStatus ===
   * "error"` — the real backend `ApiError.message`, or a fixed fallback.
   * Never raw transport/exception text. */
  historyError?: string;
}

/** Expandable, sanitized run trace (pre-4H milestone) — see Chat.runTraces
 * module docstring. Never model chain-of-thought: every `label` here is
 * a deterministic, past-tense, already-sanitized fact the backend
 * decided to report (see RunTrace.tsx). */
export interface RunTraceStep {
  stepId: string;
  category: string;
  label: string;
  status: "completed" | "warning" | "failed";
  safeMetadata?: TraceStepSafeMetadata;
}

export interface RunTraceRecord {
  /** The backend's own run_id, once known — diagnostics only, mirrors
   * `ChatRunState.serverRunId`. */
  serverRunId?: string;
  /** Chronological — steps are appended as trace.step events arrive,
   * never reordered/sorted client-side. */
  steps: RunTraceStep[];
  /** Frozen at run.completed — `elapsedSecondsSince(runStartedAt)` at
   * that moment, reusing the same timing source as the live counter
   * (see lib/elapsedTime.ts). `undefined` while the run this trace
   * belongs to is still active — that's the "live" vs. "completed"
   * presentation switch (see RunTrace.tsx). Never re-incremented once
   * set. */
  finalDurationSeconds?: number;
  /** Set together with `finalDurationSeconds` — which terminal header
   * ("Worked for Xs" vs. "Stopped after Xs") the completed trace uses.
   * `undefined` while still live. `"stopped"` is a user-initiated abort
   * (the Stop control) — distinct from `"error"` (a genuine backend/
   * transport failure) even though both currently render the same
   * "Stopped after Xs" text; kept as its own value so the distinction is
   * available without re-deriving it from anything else. */
  outcome?: "ok" | "error" | "stopped";
  /** Local, presentation-only expand/collapse choice — never sent to the
   * backend, never reset by a later user message (unlike actionCards/
   * selectionCards' auto-collapse-on-new-message behavior — instruction
   * section 36 explicitly asks that a RunTrace's own expand/collapse
   * choice be preserved instead). Starts collapsed. */
  expanded: boolean;
}

/** Interaction-capability extension — see Chat.selectionCards. */
export interface SelectionCardRecord {
  /** Which selection this specific card represents. */
  selectionId: string;
  /** Snapshot of the backend DTO for this card. Kept in sync while this
   * selection remains current; frozen at its last known value once a
   * later turn moves past it. */
  pendingSelection: PendingSelectionDTO;
  /** Frontend-only in-flight/terminal presentation state for this card —
   * see `SelectionCardState`. */
  selectionCard?: SelectionCardState | null;
  /** Collapsed to a one-line status summary — same rules as
   * ActionCardRecord.collapsed (auto-collapsed on a new user message,
   * toggled manually, never timer-based). */
  collapsed: boolean;
}

export type SelectionCardPhase = "choosing" | "skipping" | "resolved" | "skipped" | "failed";

export interface SelectionCardState {
  /** Which selection this lifecycle state tracks. */
  selectionId: string;
  phase: SelectionCardPhase;
  /** Only for "failed" — the real backend message, or a fixed fallback. */
  message?: string;
  /** Only for "resolved" — the chosen candidate's human-readable label,
   * exactly as ChooseSelectionResponse.selected_label returned it. */
  selectedLabel?: string;
}

/** Phase 4G hardening pass — see Chat.actionCards. */
export interface ActionCardRecord {
  /** Which proposal this specific card represents — used to find this
   * record again (from an approve/reject/execute response) and to decide
   * whether it's still the chat's currently actionable proposal (compare
   * against `Chat.pendingAction.proposal_id`). */
  proposalId: string;
  /** Snapshot of the backend DTO for this card. Kept in sync while this
   * proposal remains the chat's live one; frozen at its last known value
   * once a newer proposal (attached to a different message) supersedes
   * it — an older card's historical status is never silently updated by
   * an unrelated conversation turn. */
  pendingAction: PendingActionDTO;
  /** Frontend-only in-flight/terminal presentation state for this card —
   * see `ApprovalCardState`. */
  approvalCard?: ApprovalCardState | null;
  /** Collapsed to a one-line status summary. Set automatically when a
   * newer user message is sent (every OTHER card collapses; a
   * brand-new card starts expanded) and toggled manually by clicking the
   * card's own header. Never timer-based. */
  collapsed: boolean;
}

/** Phase 4G — see ActionCardRecord.approvalCard. */
export type ApprovalCardPhase = "approving" | "executing" | "completed" | "rejecting" | "expired" | "failed" | "unconfirmed";

export interface ApprovalCardState {
  /** Which proposal this lifecycle state tracks — matches the owning
   * ActionCardRecord.proposalId. */
  proposalId: string;
  phase: ApprovalCardPhase;
  /** Only for "expired"/"failed"/"unconfirmed" — the real backend
   * `userMessage`, or a fixed, pre-written fallback string. Never
   * frontend-invented beyond that fallback. */
  message?: string;
  /** Only for "completed" — mirrors ExecuteActionResponse.executed_action;
   * never fabricated. */
  executedAction?: { chatId: string | null; title: string | null; webUrl: string | null } | null;
}

/** Phase 4F — ephemeral per-chat state for one in-flight real backend
 * run. See Chat.run. */
export interface ChatRunState {
  /** Client-minted guard token (created before any network call — the
   * backend's own run_id isn't known until run.started arrives, so it
   * can't gate synchronous state at send time). Every backend-driven
   * state update carries this; a stale/mismatched token is dropped. */
  runToken: string;
  /** The backend's own run_id, once known — diagnostics only, never used
   * for the staleness guard above. */
  serverRunId?: string;
  /** Which placeholder assistant message this run is filling. */
  assistantMessageId: string;
  /** The single "current activity" line — replaced (never appended) on
   * each status event, cleared on status.clear or the first
   * message.delta. null before the first status event arrives. */
  currentActivity: { stage: string; label: string } | null;
  /** Client `Date.now()` at the moment this run began (SEND_MESSAGE/
   * EDIT_MESSAGE dispatch, before any network call) — the sole source
   * for the elapsed-time counter (Phase 4G interaction-capability
   * extension). Deliberately client time, not the backend's own
   * `run.started` timestamp: elapsed timing starts when the user
   * submits, not once the server acknowledges. Survives unchanged across
   * every `currentActivity` replacement for this run — never reset by a
   * status change. Has nothing to do with proposal/approval expiry. */
  runStartedAt: number;
}

export interface Project {
  id: string;
  name: string;
  /** Short "what this project is for" blurb, shown on the projects grid.
   * Distinct from `instructionsText`, which steers the assistant. */
  description?: string;
  instructionsText: string;
  connectorIds: string[];
  fileIds: string[];
  createdAt: number;
  pinned?: boolean;
  pinnedAt?: number;
}

export interface ProjectFile {
  id: string;
  name: string;
  size: string;
  type: string;
  selectedAt: number;
  /** Text content shown in the file preview dialog — read from the real
   * file for text-like types, otherwise a labeled mock placeholder. */
  content: string;
}

export type ScheduledTaskFrequency = "manual" | "daily" | "weekdays" | "weekly" | "monthly";
export type ScheduledTaskPermission = "manual_approve" | "auto_run";

/**
 * A typed endpoint inside a connector. The same shape backs all three
 * workspace features: a scheduled task reads several on a cadence, a chat
 * reads one ad hoc, and a distribution writes to one.
 */
export type SourceKindId =
  | "outlook_calendar"
  | "outlook_inbox"
  | "teams_channel"
  | "sharepoint_folder";

export interface TaskSource {
  id: string;
  kind: SourceKindId;
  /** Which slice of the endpoint: a range ("Today"), a folder ("NOC
   * Handover"), or a channel ("Ops Bridge"). Always one of the kind's
   * `scopeOptions`, so a run is guaranteed to find fixture content. */
  scope: string;
}

export type DestinationKindId = "teams_channel" | "email";

/** Where a result gets posted. Replaces the old free-text
 * teamsChatRoom/inboxEmail pair. */
export interface Distribution {
  kind: DestinationKindId;
  /** Channel name or email address. */
  target: string;
}

/** One execution of a scheduled task. */
export interface TaskRun {
  id: string;
  startedAt: number;
  status: "running" | "completed";
  /** The assistant message in the task's chat carrying this run's output. */
  messageId: string;
  summary?: string;
  /** Connectors that were unavailable at run time — explains a thin brief. */
  skippedConnectorIds?: string[];
  trigger: "manual" | "seed";
}

/** A mocked recurring task. There is no real scheduler: runs happen only via
 * "Run now" and the single backdated run seeded at creation. */
export interface ScheduledTask {
  id: string;
  /** Optional — a scheduled task can stand alone, not just live inside a
   * project (matches the reference product's global "Scheduled tasks" list). */
  projectId?: string;
  name: string;
  instructions: string;
  modelId: string;
  frequency: ScheduledTaskFrequency;
  /** 24h "HH:mm", relevant for every frequency except "manual". */
  timeOfDay: string;
  permission: ScheduledTaskPermission;
  /** Structured, connector-backed inputs each run reads from. */
  sources: TaskSource[];
  /** Where results are offered for posting. Never auto-sent — each run ends
   * in a pending ActionProposal, per the explicit-confirmation rule. */
  distributions: Distribution[];
  /** The one chat this task owns. Runs append to it and the user can ask
   * follow-ups there. Undefined until the first run creates it. */
  chatId?: string;
  /** Oldest first. */
  runs: TaskRun[];
  active: boolean;
  createdAt: number;
}

export type WorkspaceScope = { type: "general" } | { type: "project"; projectId: string };

export type SettingsSection = "usage" | "connectors" | "skills";

export type ProjectSettingsSection = "name" | "instructions" | "connectors" | "files";

export interface UsageBreakdownItem {
  label: string;
  percent: number;
}

export interface ConnectorUsageItem {
  label: string;
  actions: number;
}

export interface UsageSummary {
  periodLabel: string;
  messagesUsed: number;
  messagesLimit: number;
  modelBreakdown: UsageBreakdownItem[];
  thinkingBreakdown: UsageBreakdownItem[];
  connectorBreakdown: ConnectorUsageItem[];
}
