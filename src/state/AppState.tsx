import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import type {
  ActionCardRecord,
  ActionProposal,
  ActionProposalStatus,
  ActivityTrailEntry,
  Attachment,
  Chat,
  Connector,
  ConnectorUnavailableReason,
  DemoRun,
  DraftAttachment,
  DraftImageAttachment,
  Message,
  Project,
  PersistedAttachmentReference,
  ProjectFile,
  ProjectSettingsSection,
  RunTraceRecord,
  RunTraceStep,
  ScheduledTask,
  SelectionCardRecord,
  SettingsSection,
  Skill,
  TaskRun,
  TaskSource,
  ThinkingEffort,
  WorkspaceScope,
} from "../types";
import type {
  KnowledgeSourceReferenceDTO,
  PendingActionDTO,
  PendingSelectionDTO,
  SavedSessionSummaryDTO,
  SessionHistoryResponseDTO,
  SourceReferenceDTO,
  TraceStepDTO,
} from "../api/types";
import { elapsedSecondsSince } from "../lib/elapsedTime";
import {
  cancelRun,
  createSession,
  getSessionHistory,
  listSavedSessions,
  renameSession as renameSessionApi,
  rewindSession,
} from "../api/sessions";
import { deleteAttachment, uploadAttachment } from "../api/attachments";
import { ApiError } from "../api/client";
import { runBackendChat } from "../api/runBackendChat";
// `approveAction` is aliased -- an unrelated, pre-existing mock function
// of the same name already exists further down in this file (the OLD
// mock ActionProposal system, `state.actionProposals`/`cancelAction`) and
// would otherwise shadow this import.
import { approveAction as approveActionApi, executeApprovedAction, rejectAction as rejectActionApi } from "../api/approval";
import { chooseSelection as chooseSelectionApi, skipSelection as skipSelectionApi } from "../api/selections";
import { classifyApprovalFailure } from "../lib/approvalCard";
import { classifyAttachmentUploadFailure } from "../lib/attachmentError";
import { classifySelectionFailure } from "../lib/selectionCard";
import { createId } from "../lib/id";
import { hasPersistedImageAttachment } from "../lib/persistedAttachments";
import {
  ACCEPTED_IMAGE_MIME_TYPES,
  ATTACHMENT_LIMIT_NOTICE_DURATION_MS,
  LONG_PASTE_THRESHOLD,
  MAX_DRAFT_IMAGES,
  MAX_IMAGE_BYTES,
  type AcceptedImageMimeType,
} from "../lib/constants";
import {
  ACTION_CONNECTOR_ID,
  DEFAULT_MODEL_ID,
  connectedConnectorIds,
  MOCK_CONNECTORS,
  MOCK_SKILLS,
  deriveChatTitle,
  generateMockAssistantResponse,
  type MockResponseResult,
} from "../data/mock";
import { DEMO_SCRIPTS, SCHEDULE_SETUP_OPENING_PROMPT, matchDemoScenario } from "../data/demoScript";
import {
  composeRunMessage,
  composeSourceBrief,
  formatRunLabel,
  readSources,
  skippedConnectorIds,
} from "../lib/taskRun";
import { previousOccurrence } from "../lib/scheduledTasks";
import { previewForDistribution } from "../lib/distribution";
import { findDestination, findDestinationByValue } from "../data/workspaceSources";

/** Shared by `sendMessage` and `regenerateMessage` — both end with the same
 * "turn a mock response into a COMPLETE_ASSISTANT_MESSAGE dispatch" step. */
function dispatchMockResponse(
  dispatch: React.Dispatch<Action>,
  assistantMessageId: string,
  response: MockResponseResult,
  connector: Connector | undefined,
) {
  if (response.kind === "grounded") {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: { messageId: assistantMessageId, text: response.text, citations: response.citations },
    });
  } else if (response.kind === "no_answer") {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        groundingResult: "insufficient",
        searchedScope: response.searchedScope,
      },
    });
  } else if (response.kind === "action_unavailable") {
    const reason: ConnectorUnavailableReason =
      connector?.state !== "connected" ? "global_unavailable" : "project_disabled";
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        connectorUnavailable: {
          connectorId: response.connectorId,
          connectorName: response.connectorName,
          reason,
        },
      },
    });
  } else {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        // Normalised to undefined when empty, matching runScheduledTask — an
        // empty array would still satisfy the render guard downstream.
        suggestedConnectorIds:
          response.kind === "schedule_proposal" && response.suggestedConnectorIds.length > 0
            ? response.suggestedConnectorIds
            : undefined,
        actionProposal: { id: createId("action"), ...response.proposal },
      },
    });
  }
}

const DEFAULT_THINKING_EFFORT: ThinkingEffort = "instant";
/** Phase 2 (Runtime Activity Truthfulness) — the activity trail is a
 * compact live-progress affordance, not a verbose debug trace (instruction
 * section 19). Chosen after weighing readability (a long list stops being
 * scannable) against not truncating a genuinely long, factual sequence too
 * aggressively for a complex combined Teams+Knowledge+Case turn. */
const ACTIVITY_TRAIL_MAX_ENTRIES = 10;

/** UI PRESENTATION CORRECTION (post-Phase-2) — the category tag used for
 * every `RunTraceStep` synthesized from a run's own `activityTrail` at
 * completion time (see `mergeActivityTrailIntoRunTraceSteps` below). A
 * plain, honest internal classification value, never rendered to the
 * user (RunTrace.tsx's step rows only ever read `label`/`status`) — kept
 * distinct from any backend-assigned `TraceCategory` value so an
 * activity-derived row is never confused with a genuine backend
 * `trace.step` category. */
const ACTIVITY_DERIVED_TRACE_STEP_CATEGORY = "activity";

/** UI PRESENTATION CORRECTION (post-Phase-2) — folds a completed run's
 * own truthful, already-accumulated `activityTrail` (Phase 2's ephemeral,
 * live-only progress line — never shown as a disclosure while live, see
 * RunTrace.tsx/CurrentActivity.tsx) into that same message's permanent
 * `RunTraceRecord.steps`, exactly once, at the moment the run reaches a
 * terminal outcome (`BACKEND_RUN_COMPLETED`/`RUN_STOPPED` below — the
 * SAME moment `finalDurationSeconds`/`outcome` are frozen).
 *
 * This is the "safe, deterministic model" instruction section 7 asks
 * for: every entry is a verbatim label the backend already sent and the
 * user already saw live — nothing invented, nothing re-derived from
 * anything else. `RunTraceRecord`/`RunTraceStep` are themselves already
 * frontend-only, current-session-only state (never rehydrated from saved
 * chat history — see session_history_service's own DTOs, which carry no
 * run-trace field), the exact same durability class the ephemeral
 * `activityTrail` already has — so this merge does not "persist ephemeral
 * activity" in any deeper sense than the pre-existing RunTraceStep
 * mechanism already does; it simply moves already-true, already-received
 * data from one frontend-only field to another, once, at completion.
 *
 * Ordering: the trail's own entries (chronologically first — they cover
 * the tool activity that happens WHILE a run is executing) are placed
 * ahead of whatever genuine `trace.step` milestones already exist for
 * this message (which are largely response-level summaries — e.g.
 * "Generated the response" — that are only known once the run is
 * finishing). This reproduces the accepted example exactly: Processing
 * your request / Searching governed knowledge / Reviewing retrieved
 * knowledge / Validating supporting evidence (from the trail), followed
 * by Generated the response (a genuine, pre-existing trace step).
 *
 * Dedup: an activity-trail label that exactly matches an already-existing
 * trace-step label is dropped (instruction section 8 — "no duplicates"),
 * on top of the dedup the trail and the trace-step recorder already each
 * do independently. Never deduplicates two DIFFERENT labels merely
 * because they look similar. */
function mergeActivityTrailIntoRunTraceSteps(
  activityTrail: ActivityTrailEntry[],
  existingSteps: RunTraceStep[],
): RunTraceStep[] {
  if (activityTrail.length === 0) return existingSteps;
  const existingLabels = new Set(existingSteps.map((step) => step.label));
  const activitySteps: RunTraceStep[] = activityTrail
    .filter((entry) => !existingLabels.has(entry.label))
    .map((entry, index) => ({
      stepId: `activity-${index}`,
      category: ACTIVITY_DERIVED_TRACE_STEP_CATEGORY,
      label: entry.label,
      status: "completed",
    }));
  if (activitySteps.length === 0) return existingSteps;
  return [...activitySteps, ...existingSteps];
}

interface DraftState {
  text: string;
  attachments: DraftAttachment[];
  /** Chat rooms / calendars / folders attached via the composer's "+" menu,
   * read once when the message is sent. */
  sources: TaskSource[];
  thinkingEffort: ThinkingEffort;
  skillId: string | null;
  connectorIds: string[];
  modelId: string;
  /** POST-5.1 B3 overflow-UX closure pass — a brief, non-blocking message
   * shown when `queueImageFiles` rejects one or more files purely because
   * the draft is already at (or would exceed) `MAX_DRAFT_IMAGES`, never for
   * a format/size rejection (those already surface per-attachment via that
   * draft's own `error`). Transient, frontend-only UI state — never sent to
   * the backend, never part of a message. Lives on `DraftState` (rather
   * than a one-off ref) specifically so every existing draft-reset path
   * (send, new chat, chat switch — all of which already construct a fresh
   * `freshDraft()`) clears it for free, with no new cleanup call needed.
   * `key` is a fresh id per occurrence (never reused) so the auto-dismiss
   * effect restarts its timer even if the SAME message text is triggered
   * twice in a row — a plain string dependency wouldn't change in that
   * case, and the second notice would inherit the first one's remaining
   * time instead of getting its own full display duration.
   */
  attachmentLimitNotice: { message: string; key: string } | null;
}

function freshDraft(): DraftState {
  return {
    text: "",
    attachments: [],
    sources: [],
    thinkingEffort: DEFAULT_THINKING_EFFORT,
    skillId: null,
    connectorIds: [],
    modelId: DEFAULT_MODEL_ID,
    attachmentLimitNotice: null,
  };
}

export interface AppState {
  chats: Record<string, Chat>;
  chatOrder: string[];
  messages: Record<string, Message>;
  projects: Record<string, Project>;
  projectOrder: string[];
  projectFiles: Record<string, ProjectFile>;
  scheduledTasks: Record<string, ScheduledTask>;
  connectors: Record<string, Connector>;
  skills: Record<string, Skill>;
  skillOrder: string[];
  actionProposals: Record<string, ActionProposal>;
  settingsModal: { open: boolean; section: SettingsSection };
  projectSettingsModal: { open: boolean; section: ProjectSettingsSection };
  workspaceScope: WorkspaceScope;
  activeChatId: string | null;
  sidebarCollapsed: boolean;
  /** Full-page browsers shown instead of the normal workspace/project-home
   * content — mutually exclusive with each other and with a normal chat. */
  mainView: "workspace" | "allProjects" | "scheduledTasks" | "scheduledTaskDetail";
  /** Which task "scheduledTaskDetail" is currently showing. */
  activeScheduledTaskId: string | null;
  /** Timestamp of the last "you're already here" composer nudge — bumped
   * when New chat is clicked while already on a fresh, empty chat. 0 means
   * never nudged yet, so PromptComposer's effect doesn't fire on mount. */
  composerNudgeAt: number;
  draft: DraftState;
  /** POST-5.1 B4C correction pass — the GLOBAL `GET /api/sessions` boot
   * (and retry) status, distinct from any single chat's own
   * `historyHydrationStatus`. Starts `"loading"` so the sidebar's Chats
   * section can tell "still finding out" apart from "found zero saved
   * chats" apart from "the request itself failed" — a silent/ambiguous
   * empty state during a real network failure previously looked
   * identical to a genuinely empty account. */
  savedChatsHydrationStatus: "loading" | "loaded" | "error";
  /** Safe, already user-facing message for `savedChatsHydrationStatus ===
   * "error"` — the real `ApiError.message`, or a fixed fallback. Never
   * raw transport/exception text. */
  savedChatsHydrationError?: string;
}

// Exported (alongside `reducer` below) so AppState.reducer.test.ts can
// drive the real reducer with the real starting state, rather than a
// hand-maintained fixture that risks drifting from AppState's actual shape.
export const initialState: AppState = {
  chats: {},
  chatOrder: [],
  messages: {},
  projects: {},
  projectOrder: [],
  projectFiles: {},
  scheduledTasks: {},
  connectors: Object.fromEntries(MOCK_CONNECTORS.map((c) => [c.id, c])),
  skills: Object.fromEntries(MOCK_SKILLS.map((s) => [s.id, s])),
  skillOrder: MOCK_SKILLS.map((s) => s.id),
  actionProposals: {},
  settingsModal: { open: false, section: "usage" },
  projectSettingsModal: { open: false, section: "name" },
  workspaceScope: { type: "general" },
  activeChatId: null,
  sidebarCollapsed: false,
  mainView: "workspace",
  activeScheduledTaskId: null,
  composerNudgeAt: 0,
  draft: freshDraft(),
  savedChatsHydrationStatus: "loading",
};

export type Action =
  | {
      type: "SEND_MESSAGE";
      payload: {
        chatId: string;
        isNewChat: boolean;
        userMessageId: string;
        assistantMessageId: string;
        text: string;
        attachments: Attachment[];
        sources?: TaskSource[];
        timestamp: number;
        demoRun?: DemoRun;
        /** Phase 4F — present only when this send takes the real-backend
         * path (general workspace scope, no attached sources, no active
         * demo script). Establishes chat.run synchronously in this same
         * dispatch, before any network call, so the composer's
         * duplicate-send guard and activity indicator are live
         * immediately. */
        runToken?: string;
        /** POST-5.1 B5 — the LOCKED B4D invariant: a just-sent image-
         * bearing turn's user message must carry `persistedAttachments`
         * immediately, using the already-known successful upload
         * metadata (never waiting for a browser refresh/B4D rehydration
         * to populate it) — see AppState.tsx's `sendMessage` for how this
         * is built from the draft's own READY image attachments, in
         * draft order. Metadata only — never a File/Blob/draft objectUrl. */
        persistedAttachments?: PersistedAttachmentReference[];
      };
    }
  | {
      /** Always starts a brand-new, standalone chat running the
       * "schedule-setup" script — unlike SEND_MESSAGE, never reuses
       * whatever chat/project happened to be active beforehand. */
      type: "START_SCHEDULING_CHAT";
      payload: {
        chatId: string;
        userMessageId: string;
        assistantMessageId: string;
        text: string;
        timestamp: number;
      };
    }
  | {
      type: "COMPLETE_ASSISTANT_MESSAGE";
      payload: {
        messageId: string;
        text: string;
        citations?: Message["citations"];
        groundingResult?: Message["groundingResult"];
        searchedScope?: string[];
        connectorUnavailable?: Message["connectorUnavailable"];
        suggestedConnectorIds?: string[];
        actionProposal?: {
          id: string;
          connectorId: string;
          actionType: string;
          title: string;
          fields: ActionProposal["fields"];
          body: string;
          scheduledTaskDraft?: ActionProposal["scheduledTaskDraft"];
        };
        /** When present, advances the owning chat's demo script to this step. */
        demoStep?: number;
      };
    }
  | { type: "REGENERATE_MESSAGE"; payload: { messageId: string } }
  | {
      type: "EDIT_MESSAGE";
      payload: {
        chatId: string;
        messageId: string;
        text: string;
        assistantMessageId: string;
        /** True when editing the chat's first message — re-derives the
         * chat title and drops any demo script, same as a fresh opening. */
        retitle: boolean;
        /** Present only for a backend-sourced chat (mirrors SEND_MESSAGE's
         * own `runToken`) — seeds a fresh `chat.run` so the truncated
         * chat immediately shows the new assistant placeholder as
         * in-flight, exactly like a normal backend send. */
        runToken?: string;
      };
    }
  | { type: "NEW_CHAT" }
  | { type: "NUDGE_COMPOSER"; payload: { timestamp: number } }
  | { type: "SELECT_CHAT"; payload: { chatId: string } }
  | { type: "SET_DRAFT_TEXT"; payload: { text: string } }
  | { type: "SET_THINKING_EFFORT"; payload: { value: ThinkingEffort } }
  | { type: "SET_SKILL"; payload: { skillId: string | null } }
  | { type: "SET_MODEL"; payload: { modelId: string } }
  | { type: "TOGGLE_PIN"; payload: { chatId: string; timestamp: number } }
  | { type: "TOGGLE_UNREAD"; payload: { chatId: string } }
  | { type: "RENAME_CHAT"; payload: { chatId: string; title: string } }
  | { type: "DELETE_CHAT"; payload: { chatId: string } }
  | { type: "TOGGLE_CONNECTOR"; payload: { connectorId: string } }
  | { type: "ADD_ATTACHMENTS"; payload: { attachments: DraftAttachment[] } }
  | { type: "REMOVE_ATTACHMENT"; payload: { id: string } }
  // POST-5.1 B3 — patches one in-progress DraftImageAttachment (upload
  // state transitions, the durable attachmentId once known, a safe error
  // message on failure). Never touches any other draft attachment.
  | { type: "UPDATE_DRAFT_IMAGE_ATTACHMENT"; payload: { id: string; patch: Partial<DraftImageAttachment> } }
  // POST-5.1 B3 overflow-UX closure pass — a brief, non-blocking notice
  // that `queueImageFiles` rejected one or more files purely because the
  // draft is already at MAX_DRAFT_IMAGES. `CLEAR_...` is dispatched either
  // by the auto-dismiss timeout or implicitly by any action that replaces
  // `state.draft` wholesale (send, new chat, etc. all call `freshDraft()`).
  | { type: "SET_ATTACHMENT_LIMIT_NOTICE"; payload: { message: string; key: string } }
  | { type: "CLEAR_ATTACHMENT_LIMIT_NOTICE" }
  // POST-5.1 B3 — an empty chat created purely because the user attached
  // an image before ever sending a first message (mirrors "the first
  // submitted prompt creates the conversation" — here, the first
  // attachment does). Idempotent: a no-op if `chatId` already exists.
  | { type: "CREATE_DRAFT_CHAT"; payload: { chatId: string; timestamp: number } }
  // POST-5.1 B3 — sets `Chat.backendSessionId` OUTSIDE of an active run
  // (unlike `BACKEND_SESSION_CREATED`, this has no `runToken`/
  // `withActiveRun` guard — attachment upload isn't a "run"). Never
  // clobbers an already-set `backendSessionId` (a second, superseded
  // in-flight session-creation call must never overwrite the first).
  | { type: "BACKEND_SESSION_ENSURED"; payload: { chatId: string; sessionId: string } }
  // POST-5.1 B4C — merges `GET /api/sessions`'s own summaries (already
  // owner-scoped, empty-session-excluded, sorted newest activity first)
  // into state at boot. Dedupes by `backendSessionId`, never by `Chat.id`
  // equality alone (see AppState.tsx's reducer case) — safe to dispatch
  // more than once (StrictMode double-invocation) without ever producing a
  // duplicate chat or re-ordering an already-hydrated one.
  | { type: "HYDRATE_SAVED_SESSIONS"; payload: { sessions: SavedSessionSummaryDTO[] } }
  // POST-5.1 B4C correction pass — the GLOBAL saved-chat-list request
  // lifecycle (see AppState.savedChatsHydrationStatus's own docstring).
  // STARTED is dispatched only by an explicit retry (the initial state is
  // already "loading", so boot never needs to dispatch it itself).
  | { type: "SAVED_CHATS_HYDRATION_STARTED" }
  | { type: "SAVED_CHATS_HYDRATION_FAILED"; payload: { message: string } }
  | { type: "HISTORY_FETCH_STARTED"; payload: { chatId: string } }
  | { type: "HISTORY_FETCH_SUCCEEDED"; payload: { chatId: string; response: SessionHistoryResponseDTO } }
  | { type: "HISTORY_FETCH_FAILED"; payload: { chatId: string; message: string } }
  | { type: "RETRY_HISTORY_LOAD"; payload: { chatId: string } }
  | { type: "TOGGLE_SIDEBAR" }
  | {
      type: "CREATE_PROJECT";
      payload: { projectId: string; name: string; description?: string; timestamp: number };
    }
  | { type: "ENTER_PROJECT"; payload: { projectId: string } }
  | { type: "SET_PROJECT_INSTRUCTIONS"; payload: { projectId: string; text: string } }
  | {
      type: "RENAME_PROJECT";
      payload: { projectId: string; name: string; description?: string };
    }
  | { type: "DELETE_PROJECT"; payload: { projectId: string } }
  | { type: "TOGGLE_PROJECT_PINNED"; payload: { projectId: string; timestamp: number } }
  | { type: "TOGGLE_PROJECT_CONNECTOR"; payload: { projectId: string; connectorId: string } }
  | { type: "ADD_PROJECT_FILES"; payload: { projectId: string; files: ProjectFile[] } }
  | { type: "REMOVE_PROJECT_FILE"; payload: { projectId: string; fileId: string } }
  | {
      /** Begins a run. The owned chat is resolved *inside the reducer* from
       * live state — see the case body for why the pre-minted chatId can't be
       * trusted. */
      type: "START_TASK_RUN";
      payload: {
        taskId: string;
        runId: string;
        chatId: string;
        messageId: string;
        speakerLabel: string;
        timestamp: number;
        focus: boolean;
      };
    }
  | {
      type: "COMPLETE_TASK_RUN";
      payload: { taskId: string; runId: string; summary: string; skippedConnectorIds?: string[] };
    }
  | {
      /** Inserts an already-complete, backdated run so a new task has
       * something to show without pretending a live run just happened. */
      type: "SEED_TASK_RUN";
      payload: {
        taskId: string;
        runId: string;
        chatId: string;
        messageId: string;
        text: string;
        summary: string;
        speakerLabel: string;
        ranAt: number;
        suggestedConnectorIds?: string[];
      };
    }
  | {
      type: "PROPOSE_DISTRIBUTION";
      payload: {
        proposalId: string;
        messageId: string;
        connectorId: string;
        destinationLabel: string;
        body: string;
      };
    }
  | { type: "ADD_DRAFT_SOURCE"; payload: { source: TaskSource } }
  | { type: "REMOVE_DRAFT_SOURCE"; payload: { id: string } }
  | { type: "CREATE_SCHEDULED_TASK"; payload: ScheduledTask }
  | { type: "UPDATE_SCHEDULED_TASK"; payload: ScheduledTask }
  | { type: "DELETE_SCHEDULED_TASK"; payload: { id: string } }
  | { type: "TOGGLE_SCHEDULED_TASK_ACTIVE"; payload: { id: string } }
  | { type: "OPEN_ALL_PROJECTS" }
  | { type: "CLOSE_ALL_PROJECTS" }
  | { type: "OPEN_SCHEDULED_TASKS" }
  | { type: "VIEW_SCHEDULED_TASK"; payload: { id: string } }
  | {
      type: "MOVE_CHAT_TO_PROJECT";
      payload: { chatId: string; projectId: string | null; timestamp: number };
    }
  | { type: "CONNECT_CONNECTOR"; payload: { connectorId: string } }
  | { type: "DISCONNECT_CONNECTOR"; payload: { connectorId: string } }
  | {
      type: "CREATE_SKILL";
      payload: { skillId: string; name: string; description: string; instructions: string };
    }
  | {
      type: "UPDATE_SKILL";
      payload: { skillId: string; name: string; description: string; instructions: string };
    }
  | { type: "DELETE_SKILL"; payload: { skillId: string } }
  | {
      type: "SET_ACTION_PROPOSAL_STATUS";
      payload: { id: string; status: ActionProposalStatus; createdTaskId?: string };
    }
  | { type: "OPEN_SETTINGS"; payload: { section?: SettingsSection } }
  | { type: "CLOSE_SETTINGS" }
  | { type: "SET_SETTINGS_SECTION"; payload: { section: SettingsSection } }
  | { type: "OPEN_PROJECT_SETTINGS"; payload: { section?: ProjectSettingsSection } }
  | { type: "CLOSE_PROJECT_SETTINGS" }
  | { type: "SET_PROJECT_SETTINGS_SECTION"; payload: { section: ProjectSettingsSection } }
  // --- Phase 4F: real backend streaming (general-scope chats only) ---
  | { type: "BACKEND_SESSION_CREATED"; payload: { chatId: string; runToken: string; sessionId: string } }
  | { type: "BACKEND_RUN_STARTED"; payload: { chatId: string; runToken: string; serverRunId: string } }
  | {
      type: "BACKEND_STATUS_UPDATE";
      payload: { chatId: string; runToken: string; stage: string; label: string; activityKind: string | null };
    }
  | { type: "BACKEND_STATUS_CLEAR"; payload: { chatId: string; runToken: string } }
  | {
      type: "BACKEND_MESSAGE_DELTA";
      payload: { chatId: string; runToken: string; messageId: string; textDelta: string };
    }
  | {
      type: "BACKEND_MESSAGE_COMPLETED";
      payload: {
        chatId: string;
        runToken: string;
        messageId: string;
        content: string;
        source?: SourceReferenceDTO;
        knowledgeSources?: KnowledgeSourceReferenceDTO[];
      };
    }
  | {
      type: "BACKEND_ACTION_PENDING";
      payload: { chatId: string; runToken: string; messageId: string; action: PendingActionDTO };
    }
  | {
      type: "BACKEND_SELECTION_PENDING";
      payload: { chatId: string; runToken: string; messageId: string; selection: PendingSelectionDTO };
    }
  // Hardening pass: begins a resumed READ turn after a Teams chat
  // selection resolves — mirrors SEND_MESSAGE's run-seeding, but
  // deliberately creates ONLY a new assistant placeholder message, never
  // a paired user message (selecting a candidate is a UI interaction,
  // not a new conversational user utterance — see AppState.tsx's
  // `chooseSelectionOption`).
  | {
      type: "BEGIN_READ_RESUME";
      payload: { chatId: string; assistantMessageId: string; runToken: string; timestamp: number };
    }
  | {
      type: "BACKEND_RUN_ERROR";
      payload: { chatId: string; runToken: string; messageId: string; message: string };
    }
  | {
      type: "BACKEND_RUN_COMPLETED";
      payload: { chatId: string; runToken: string; outcome: "ok" | "error" };
    }
  // --- Expandable, sanitized run trace (pre-4H milestone) ---
  | {
      type: "BACKEND_TRACE_STEP";
      payload: { chatId: string; runToken: string; messageId: string; step: TraceStepDTO };
    }
  | { type: "TOGGLE_RUN_TRACE_EXPANDED"; payload: { chatId: string; messageId: string } }
  // User-initiated Stop control (pre-4H refinement) — client transport
  // abort only; see `stopActiveRun`'s own docstring for what this can
  // and cannot guarantee about backend execution.
  | { type: "RUN_STOPPED"; payload: { chatId: string; runToken: string } }
  // --- Phase 4G: real approval card (approve -> execute lifecycle) ---
  | { type: "APPROVAL_APPROVE_STARTED"; payload: { chatId: string; proposalId: string } }
  | {
      type: "APPROVAL_APPROVE_SUCCEEDED";
      payload: { chatId: string; proposalId: string; pendingAction: PendingActionDTO | null };
    }
  | {
      type: "APPROVAL_EXECUTE_SUCCEEDED";
      payload: {
        chatId: string;
        proposalId: string;
        pendingAction: PendingActionDTO | null;
        executedAction: { chatId: string | null; title: string | null; webUrl: string | null } | null;
      };
    }
  | { type: "APPROVAL_REJECT_STARTED"; payload: { chatId: string; proposalId: string } }
  | {
      type: "APPROVAL_REJECT_SUCCEEDED";
      payload: { chatId: string; proposalId: string; pendingAction: PendingActionDTO | null };
    }
  | {
      type: "APPROVAL_REQUEST_FAILED";
      payload: { chatId: string; proposalId: string; phase: "expired" | "failed" | "unconfirmed"; message: string };
    }
  | { type: "TOGGLE_ACTION_CARD_COLLAPSED"; payload: { chatId: string; messageId: string } }
  // --- Interaction-capability extension: Teams chat-name selection ---
  | { type: "SELECTION_CHOOSE_STARTED"; payload: { chatId: string; selectionId: string } }
  | {
      type: "SELECTION_CHOOSE_SUCCEEDED";
      payload: {
        chatId: string;
        selectionId: string;
        selectedLabel: string;
        pendingAction: PendingActionDTO | null;
      };
    }
  | { type: "SELECTION_SKIP_STARTED"; payload: { chatId: string; selectionId: string } }
  | { type: "SELECTION_SKIP_SUCCEEDED"; payload: { chatId: string; selectionId: string } }
  | { type: "SELECTION_REQUEST_FAILED"; payload: { chatId: string; selectionId: string; message: string } }
  | { type: "TOGGLE_SELECTION_CARD_COLLAPSED"; payload: { chatId: string; messageId: string } };

/** Phase 4F reducer guard: drops the update if `chatId` no longer exists,
 * or its active run's token doesn't match `runToken` (a stale/superseded
 * run — see ChatRunState's docstring in types.ts) — never mutates state
 * on behalf of a run that isn't (or is no longer) the chat's current one. */
function withActiveRun(
  state: AppState,
  chatId: string,
  runToken: string,
  mutate: (chat: Chat) => Chat,
): AppState {
  const chat = state.chats[chatId];
  if (!chat || !chat.run || chat.run.runToken !== runToken) return state;
  return { ...state, chats: { ...state.chats, [chatId]: mutate(chat) } };
}

/** Phase 4G reducer guard: drops the update if `chatId` no longer exists,
 * or its current `pendingAction.proposal_id` doesn't match `proposalId` —
 * a stale in-flight approve/reject/execute request (superseded by a
 * newer proposal while it was in flight) can never mutate a different
 * proposal's card.
 *
 * Hardening pass: the card itself now lives in `chat.actionCards`, keyed
 * by the message that owns it — resolved via `chat.pendingActionMessageId`
 * (always in sync with `chat.pendingAction`, see BACKEND_ACTION_PENDING).
 * `mutateCard` updates that one record; `mutateChat`, if given, also
 * updates chat-level fields (kept as the authoritative "current backend
 * state" per the frozen `pendingAction` field — never itself used for
 * transcript rendering). */
function withMatchingProposal(
  state: AppState,
  chatId: string,
  proposalId: string,
  mutateCard: (record: ActionCardRecord) => ActionCardRecord,
  mutateChat?: (chat: Chat) => Chat,
): AppState {
  const chat = state.chats[chatId];
  if (!chat || chat.pendingAction?.proposal_id !== proposalId) return state;
  const messageId = chat.pendingActionMessageId;
  const record = messageId ? chat.actionCards?.[messageId] : undefined;
  if (!messageId || !record) return state;

  const baseChat = mutateChat ? mutateChat(chat) : chat;
  return {
    ...state,
    chats: {
      ...state.chats,
      [chatId]: {
        ...baseChat,
        actionCards: { ...baseChat.actionCards, [messageId]: mutateCard(record) },
      },
    },
  };
}

/** Phase 4G hardening pass — returns a new actionCards map with every
 * entry's `collapsed` flag set to true. Used only when a new user message
 * is sent (never a timer); a fresh card for a brand-new proposal is added
 * separately, already expanded (see BACKEND_ACTION_PENDING). A no-op
 * (referentially, per-entry) for any entry already collapsed. */
function collapseAllActionCards(actionCards: Record<string, ActionCardRecord>): Record<string, ActionCardRecord> {
  const next: Record<string, ActionCardRecord> = {};
  for (const [messageId, record] of Object.entries(actionCards)) {
    next[messageId] = record.collapsed ? record : { ...record, collapsed: true };
  }
  return next;
}

/** Interaction-capability extension — mirrors `withMatchingProposal`
 * exactly, using `pendingSelection`/`selectionCards`/
 * `pendingSelectionMessageId` in place of the approval equivalents. */
function withMatchingSelection(
  state: AppState,
  chatId: string,
  selectionId: string,
  mutateCard: (record: SelectionCardRecord) => SelectionCardRecord,
  mutateChat?: (chat: Chat) => Chat,
): AppState {
  const chat = state.chats[chatId];
  if (!chat || chat.pendingSelection?.selection_id !== selectionId) return state;
  const messageId = chat.pendingSelectionMessageId;
  const record = messageId ? chat.selectionCards?.[messageId] : undefined;
  if (!messageId || !record) return state;

  const baseChat = mutateChat ? mutateChat(chat) : chat;
  return {
    ...state,
    chats: {
      ...state.chats,
      [chatId]: {
        ...baseChat,
        selectionCards: { ...baseChat.selectionCards, [messageId]: mutateCard(record) },
      },
    },
  };
}

/** Mirrors `collapseAllActionCards` — see its own docstring. */
function collapseAllSelectionCards(
  selectionCards: Record<string, SelectionCardRecord>,
): Record<string, SelectionCardRecord> {
  const next: Record<string, SelectionCardRecord> = {};
  for (const [messageId, record] of Object.entries(selectionCards)) {
    next[messageId] = record.collapsed ? record : { ...record, collapsed: true };
  }
  return next;
}

/** Anchors a just-created ActionProposal (from a resolved WRITE-kind
 * Teams chat selection — see selection_service.py's `choose`) to an
 * actionCards record, exactly like BACKEND_ACTION_PENDING's own
 * "find existing owner or mint a fresh one" upsert -- but there is no
 * live assistant message for this call (it's a direct API response, not
 * a streamed turn), so `fallbackMessageId` (the selection's own owning
 * message) anchors a genuinely new proposal instead. */
function upsertActionCardForProposal(chat: Chat, fallbackMessageId: string, pendingAction: PendingActionDTO): Chat {
  const existingOwnerId = Object.keys(chat.actionCards ?? {}).find(
    (id) => chat.actionCards![id].proposalId === pendingAction.proposal_id,
  );
  const ownerId = existingOwnerId ?? fallbackMessageId;
  const existingRecord = existingOwnerId ? chat.actionCards![existingOwnerId] : undefined;

  return {
    ...chat,
    pendingAction,
    pendingActionMessageId: ownerId,
    actionCards: {
      ...chat.actionCards,
      [ownerId]: {
        proposalId: pendingAction.proposal_id,
        pendingAction,
        approvalCard: existingRecord ? existingRecord.approvalCard : undefined,
        collapsed: existingRecord ? existingRecord.collapsed : false,
      },
    },
  };
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SEND_MESSAGE": {
      const {
        chatId, isNewChat, userMessageId, assistantMessageId, text, attachments, sources, timestamp, demoRun,
        runToken, persistedAttachments,
      } = action.payload;

      const chat: Chat = isNewChat
        ? {
            id: chatId,
            title: deriveChatTitle(text),
            projectId: state.workspaceScope.type === "project" ? state.workspaceScope.projectId : null,
            pinned: false,
            createdAt: timestamp,
            messageIds: [],
            activeSkillId: state.draft.skillId,
            connectorIds: state.draft.connectorIds,
            selectedModelId: state.draft.modelId,
            thinkingEffort: state.draft.thinkingEffort,
            demoRun,
          }
        : state.chats[chatId];

      const userMessage: Message = {
        id: userMessageId,
        chatId,
        role: "user",
        text,
        status: "complete",
        attachments,
        sources: sources && sources.length > 0 ? sources : undefined,
        createdAt: timestamp,
        // POST-5.1 B5 — the LOCKED B4D invariant (immediate, not only
        // after refresh): see the action payload's own docstring.
        persistedAttachments: persistedAttachments && persistedAttachments.length > 0 ? persistedAttachments : undefined,
      };
      const assistantMessage: Message = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: timestamp + 1,
      };

      const updatedChat: Chat = {
        ...chat,
        // POST-5.1 B3: a chat may already exist with zero messages (e.g.
        // `CREATE_DRAFT_CHAT`, fired when the user attached an image
        // before ever sending anything) — `isNewChat` is false for that
        // case (an id was already active), so the branch above never ran
        // `deriveChatTitle`. Re-derive here whenever this is genuinely
        // the chat's first message, regardless of which branch created
        // the chat record itself; a chat that already has messages keeps
        // its existing title untouched, exactly as before.
        title: chat.messageIds.length === 0 ? deriveChatTitle(text) : chat.title,
        messageIds: [...chat.messageIds, userMessageId, assistantMessageId],
        // Phase 4F: seed the run only — deliberately never touches
        // pendingAction here. A prior action.pending stays visible in
        // state until a newer action.pending event authoritatively
        // replaces it (or Phase 4G's approval flow resolves it) — a new
        // run starting is not itself a resolution.
        ...(runToken ? { run: { runToken, assistantMessageId, currentActivity: null, activityTrail: [], runStartedAt: timestamp } } : null),
        // Phase 4G hardening pass: a new user message auto-collapses
        // every existing action card to its compact one-line status row
        // (never a timer) — but never moves, edits, or removes any of
        // them. Cards remain fully actionable once expanded again; see
        // ApprovalCard.tsx's `canAct`.
        ...(chat.actionCards
          ? { actionCards: collapseAllActionCards(chat.actionCards) }
          : null),
        ...(chat.selectionCards
          ? { selectionCards: collapseAllSelectionCards(chat.selectionCards) }
          : null),
        // Expandable, sanitized run trace (pre-4H milestone) — seeds an
        // empty record owned by this run's assistant message. Unlike
        // actionCards/selectionCards, existing trace records are never
        // touched here (instruction section 36: a user's own
        // expand/collapse choice on a HISTORICAL trace survives a later
        // message untouched, never auto-collapsed).
        ...(runToken
          ? { runTraces: { ...chat.runTraces, [assistantMessageId]: { steps: [], expanded: false } } }
          : null),
      };

      return {
        ...state,
        chats: { ...state.chats, [chatId]: updatedChat },
        chatOrder: isNewChat ? [chatId, ...state.chatOrder] : state.chatOrder,
        messages: {
          ...state.messages,
          [userMessageId]: userMessage,
          [assistantMessageId]: assistantMessage,
        },
        activeChatId: chatId,
        draft: { ...state.draft, text: "", attachments: [], sources: [] },
      };
    }

    case "START_SCHEDULING_CHAT": {
      const { chatId, userMessageId, assistantMessageId, text, timestamp } = action.payload;

      const chat: Chat = {
        id: chatId,
        title: "New scheduled task",
        projectId: null,
        pinned: false,
        createdAt: timestamp,
        messageIds: [userMessageId, assistantMessageId],
        activeSkillId: null,
        connectorIds: [],
        selectedModelId: DEFAULT_MODEL_ID,
        thinkingEffort: DEFAULT_THINKING_EFFORT,
        demoRun: { scenarioId: "schedule-setup", step: 0 },
      };
      const userMessage: Message = {
        id: userMessageId,
        chatId,
        role: "user",
        text,
        status: "complete",
        createdAt: timestamp,
      };
      const assistantMessage: Message = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: timestamp + 1,
      };

      return {
        ...state,
        chats: { ...state.chats, [chatId]: chat },
        chatOrder: [chatId, ...state.chatOrder],
        messages: { ...state.messages, [userMessageId]: userMessage, [assistantMessageId]: assistantMessage },
        activeChatId: chatId,
        workspaceScope: { type: "general" },
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "COMPLETE_ASSISTANT_MESSAGE": {
      const {
        messageId,
        text,
        citations,
        groundingResult,
        searchedScope,
        connectorUnavailable,
        suggestedConnectorIds,
        actionProposal,
        demoStep,
      } = action.payload;
      const existing = state.messages[messageId];
      if (!existing) return state;

      const updatedMessage: Message = {
        ...existing,
        text,
        citations,
        groundingResult,
        searchedScope,
        connectorUnavailable,
        suggestedConnectorIds,
        actionProposalId: undefined,
        status: "complete",
      };

      let nextActionProposals = state.actionProposals;
      if (actionProposal) {
        updatedMessage.actionProposalId = actionProposal.id;
        nextActionProposals = {
          ...state.actionProposals,
          [actionProposal.id]: {
            id: actionProposal.id,
            chatId: existing.chatId,
            connectorId: actionProposal.connectorId,
            actionType: actionProposal.actionType,
            title: actionProposal.title,
            fields: actionProposal.fields,
            body: actionProposal.body,
            scheduledTaskDraft: actionProposal.scheduledTaskDraft,
            status: "pending",
          },
        };
      }

      let nextChats = state.chats;
      if (demoStep !== undefined) {
        const chat = state.chats[existing.chatId];
        if (chat?.demoRun) {
          nextChats = {
            ...state.chats,
            [chat.id]: { ...chat, demoRun: { ...chat.demoRun, step: demoStep } },
          };
        }
      }

      return {
        ...state,
        messages: { ...state.messages, [messageId]: updatedMessage },
        actionProposals: nextActionProposals,
        chats: nextChats,
      };
    }

    // --- Phase 4F: real backend streaming ---

    case "BACKEND_SESSION_CREATED": {
      const { chatId, runToken, sessionId } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => ({ ...chat, backendSessionId: sessionId }));
    }

    case "BACKEND_RUN_STARTED": {
      const { chatId, runToken, serverRunId } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => ({
        ...chat,
        run: chat.run ? { ...chat.run, serverRunId } : chat.run,
      }));
    }

    case "BACKEND_STATUS_UPDATE": {
      const { chatId, runToken, stage, label, activityKind } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => {
        if (!chat.run) return chat;
        const currentActivity = { stage, label, activityKind };
        // Phase 2 (Runtime Activity Truthfulness): the trail records only
        // DISTINCT, backend-emitted transitions -- the backend's own
        // StatusTranslator already never re-sends a signature-identical
        // status back-to-back, but this guard also protects against a
        // literal duplicate SSE delivery. Bounded to
        // ACTIVITY_TRAIL_MAX_ENTRIES (oldest dropped first) so this can
        // never grow unbounded across a long-running turn.
        const previous = chat.run.activityTrail;
        const isDuplicateOfLast =
          previous.length > 0 &&
          previous[previous.length - 1].stage === stage &&
          previous[previous.length - 1].label === label &&
          previous[previous.length - 1].activityKind === activityKind;
        const activityTrail = isDuplicateOfLast
          ? previous
          : [...previous, currentActivity].slice(-ACTIVITY_TRAIL_MAX_ENTRIES);
        return { ...chat, run: { ...chat.run, currentActivity, activityTrail } };
      });
    }

    case "BACKEND_STATUS_CLEAR": {
      const { chatId, runToken } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => ({
        ...chat,
        run: chat.run ? { ...chat.run, currentActivity: null } : chat.run,
      }));
    }

    case "BACKEND_MESSAGE_DELTA": {
      const { chatId, runToken, messageId, textDelta } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || !chat.run || chat.run.runToken !== runToken) return state;
      const existing = state.messages[messageId];
      if (!existing) return state;
      return {
        ...state,
        // Defensive: the activity line disappears the instant the first
        // delta arrives even if status.clear was somehow missed.
        chats: { ...state.chats, [chatId]: { ...chat, run: { ...chat.run, currentActivity: null } } },
        messages: {
          ...state.messages,
          [messageId]: { ...existing, text: existing.text + textDelta, status: "streaming" },
        },
      };
    }

    case "BACKEND_MESSAGE_COMPLETED": {
      const { chatId, runToken, messageId, content, source, knowledgeSources } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || !chat.run || chat.run.runToken !== runToken) return state;
      const existing = state.messages[messageId];
      if (!existing) return state;
      // Phase 5.1J correction pass (Part C): `source` (Teams) and
      // `knowledgeSources` (KM) are independent — either, both, or
      // neither may be present on one completed message (a combined-
      // answer turn legitimately carries both). Each mirrors
      // actionCards/selectionCards/runTraces's exact same message-
      // ownership model: permanently owned by THIS message, never moves,
      // never duplicated, omitted entirely when this turn produced
      // nothing of that kind.
      const updatedChat =
        source || (knowledgeSources && knowledgeSources.length > 0)
          ? {
              ...chat,
              ...(source ? { sources: { ...chat.sources, [messageId]: source } } : null),
              ...(knowledgeSources && knowledgeSources.length > 0
                ? { knowledgeSources: { ...chat.knowledgeSources, [messageId]: knowledgeSources } }
                : null),
            }
          : null;
      return {
        ...state,
        // Authoritative overwrite — reconciles with, never appends to,
        // whatever accumulated delta text preceded this.
        messages: { ...state.messages, [messageId]: { ...existing, text: content, status: "complete" } },
        chats: updatedChat ? { ...state.chats, [chatId]: updatedChat } : state.chats,
      };
    }

    case "BACKEND_ACTION_PENDING": {
      const { chatId, runToken, messageId, action: pendingAction } = action.payload;
      // Deliberately not attached to the message's own fields, and never
      // mixed with the existing mock ActionProposal/actionProposalId
      // system (which already has live Approve/Cancel controls) — see
      // ApprovalCard.tsx (Phase 4G).
      return withActiveRun(state, chatId, runToken, (chat) => {
        // HARDENING PASS: `proposal_id` is the durable identity of a
        // proposal — a card belongs to whichever message FIRST reported
        // it, permanently, never to "whichever message most recently
        // reported it." Some later, unrelated turn can legitimately
        // arrive with an `action.pending` event carrying the SAME
        // `proposal_id` as an earlier, already-resolved (or still
        // pending) one — e.g. the agent re-surfacing the current backend
        // state of an existing proposal rather than proposing a new one.
        // Blindly upserting at `messageId` in that case would create a
        // SECOND card for the same proposal under the new message —
        // violating the "one proposal, one card" invariant. So: find
        // whichever message (if any) already owns this proposal_id
        // first, and update THAT record in place; only mint a brand-new
        // record, anchored to `messageId`, for a genuinely new
        // proposal_id never seen before in this chat.
        const existingOwnerId = Object.keys(chat.actionCards ?? {}).find(
          (id) => chat.actionCards![id].proposalId === pendingAction.proposal_id,
        );
        const ownerId = existingOwnerId ?? messageId;
        const existingRecord = existingOwnerId ? chat.actionCards![existingOwnerId] : undefined;

        return {
          ...chat,
          pendingAction,
          // Ownership never moves to the new message — it stays with
          // whichever message originally introduced this proposal_id.
          pendingActionMessageId: ownerId,
          actionCards: {
            ...chat.actionCards,
            [ownerId]: {
              proposalId: pendingAction.proposal_id,
              pendingAction,
              // A refresh of an already-known proposal preserves its
              // existing lifecycle/collapse state untouched — a genuinely
              // new proposal_id always starts fresh and expanded.
              approvalCard: existingRecord ? existingRecord.approvalCard : undefined,
              collapsed: existingRecord ? existingRecord.collapsed : false,
            },
          },
        };
      });
    }

    case "BACKEND_SELECTION_PENDING": {
      // Mirrors BACKEND_ACTION_PENDING's "find existing owner or mint a
      // fresh one" upsert exactly — same "one selection_id, one card,
      // never moves" invariant.
      const { chatId, runToken, messageId, selection } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => {
        const existingOwnerId = Object.keys(chat.selectionCards ?? {}).find(
          (id) => chat.selectionCards![id].selectionId === selection.selection_id,
        );
        const ownerId = existingOwnerId ?? messageId;
        const existingRecord = existingOwnerId ? chat.selectionCards![existingOwnerId] : undefined;

        return {
          ...chat,
          pendingSelection: selection,
          pendingSelectionMessageId: ownerId,
          pendingSelectionRunToken: runToken,
          selectionCards: {
            ...chat.selectionCards,
            [ownerId]: {
              selectionId: selection.selection_id,
              pendingSelection: selection,
              selectionCard: existingRecord ? existingRecord.selectionCard : undefined,
              collapsed: existingRecord ? existingRecord.collapsed : false,
            },
          },
        };
      });
    }

    // --- Expandable, sanitized run trace (pre-4H milestone) -------------

    case "BACKEND_TRACE_STEP": {
      const { chatId, runToken, messageId, step } = action.payload;
      return withActiveRun(state, chatId, runToken, (chat) => {
        const existing = chat.runTraces?.[messageId];
        const runTraceStep: RunTraceRecord["steps"][number] = {
          stepId: step.step_id,
          category: step.category,
          label: step.label,
          status: step.status,
          safeMetadata: step.safe_metadata,
        };
        return {
          ...chat,
          runTraces: {
            ...chat.runTraces,
            [messageId]: existing
              ? { ...existing, steps: [...existing.steps, runTraceStep] }
              : { steps: [runTraceStep], expanded: false },
          },
        };
      });
    }

    case "TOGGLE_RUN_TRACE_EXPANDED": {
      const { chatId, messageId } = action.payload;
      const chat = state.chats[chatId];
      const record = chat?.runTraces?.[messageId];
      if (!chat || !record) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            runTraces: { ...chat.runTraces, [messageId]: { ...record, expanded: !record.expanded } },
          },
        },
      };
    }

    case "BEGIN_READ_RESUME": {
      // Hardening pass: seeds a resumed-read run exactly like SEND_MESSAGE
      // does (chat.run, elapsed timer, card auto-collapse), but appends
      // ONLY a new assistant placeholder message — deliberately never a
      // paired user message/id, and chat.messageIds gains no user entry.
      // See this action's own docstring above (Action union) for why.
      const { chatId, assistantMessageId, runToken, timestamp } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;

      const assistantMessage: Message = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: timestamp,
      };

      return {
        ...state,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            messageIds: [...chat.messageIds, assistantMessageId],
            run: { runToken, assistantMessageId, currentActivity: null, activityTrail: [], runStartedAt: timestamp },
            ...(chat.actionCards ? { actionCards: collapseAllActionCards(chat.actionCards) } : null),
            ...(chat.selectionCards ? { selectionCards: collapseAllSelectionCards(chat.selectionCards) } : null),
            runTraces: { ...chat.runTraces, [assistantMessageId]: { steps: [], expanded: false } },
          },
        },
        messages: { ...state.messages, [assistantMessageId]: assistantMessage },
      };
    }

    case "BACKEND_RUN_ERROR": {
      const { chatId, runToken, messageId, message } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || !chat.run || chat.run.runToken !== runToken) return state;
      const existing = state.messages[messageId];
      if (!existing) return state;
      return {
        ...state,
        // Never clobbers whatever partial text already streamed in.
        messages: { ...state.messages, [messageId]: { ...existing, status: "error", errorMessage: message } },
      };
    }

    case "BACKEND_RUN_COMPLETED": {
      const { chatId, runToken, outcome } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || !chat.run || chat.run.runToken !== runToken) return state;

      const { assistantMessageId } = chat.run;
      // Interaction-capability extension: unlike `pendingAction` (durable
      // across turns until explicitly replaced/approved/rejected), a
      // pending Teams chat selection is only meaningful to act on while
      // this SAME run is the one that created/reaffirmed it —
      // `pendingSelectionRunToken` records which run that was. A run
      // that completes without having (re)set it means a LATER turn
      // moved past the selection without resolving it through this
      // card (e.g. an exact-match resolution elsewhere) — the card
      // becomes stale/non-actionable (see selectionCard.ts's
      // `deriveSelectionCardView`) rather than remaining forever
      // clickable, mirroring the backend's own `supersede_active_selection`.
      const staleSelection = Boolean(chat.pendingSelection) && chat.pendingSelectionRunToken !== runToken;
      // Expandable, sanitized run trace (pre-4H milestone): freeze this
      // run's owning trace record's duration NOW, from the same
      // client-side `runStartedAt` the live counter already used (see
      // ChatRunState.runStartedAt's own docstring) — reusing the existing
      // elapsed-timing source, never a second/unrelated timer. Once set,
      // `finalDurationSeconds` is never recomputed/incremented again;
      // this is the one and only place it's ever assigned.
      const existingTrace = chat.runTraces?.[assistantMessageId];
      const updatedRunTraces = existingTrace
        ? {
            ...chat.runTraces,
            [assistantMessageId]: {
              ...existingTrace,
              // UI PRESENTATION CORRECTION (post-Phase-2) — fold the run's
              // own truthful, live-only activity trail into the permanent
              // step history exactly once, at the same moment the trace
              // freezes (see mergeActivityTrailIntoRunTraceSteps above).
              steps: mergeActivityTrailIntoRunTraceSteps(chat.run.activityTrail, existingTrace.steps),
              finalDurationSeconds: elapsedSecondsSince(chat.run.runStartedAt),
              outcome,
            },
          }
        : chat.runTraces;
      const updatedChat: Chat = {
        ...chat,
        run: undefined, // pendingAction intentionally untouched
        runTraces: updatedRunTraces,
        ...(staleSelection
          ? { pendingSelection: null, pendingSelectionMessageId: undefined, pendingSelectionRunToken: undefined }
          : null),
      };

      let nextMessages = state.messages;
      if (outcome === "error") {
        // Defensive edge case: a run that ended in error but never
        // produced an explicit `error` event (no backend contract path
        // does this today, but a message must never be left stuck
        // showing an active/streaming state after its run is over).
        const existing = state.messages[assistantMessageId];
        if (existing && (existing.status === "pending" || existing.status === "streaming")) {
          nextMessages = {
            ...state.messages,
            [assistantMessageId]: {
              ...existing,
              status: "error",
              errorMessage: existing.errorMessage ?? "Something went wrong. Please try again.",
            },
          };
        }
      }

      return { ...state, chats: { ...state.chats, [chatId]: updatedChat }, messages: nextMessages };
    }

    // --- User-initiated Stop control (pre-4H refinement) -----------------

    case "RUN_STOPPED": {
      const { chatId, runToken } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || !chat.run || chat.run.runToken !== runToken) return state;

      const { assistantMessageId, runStartedAt, activityTrail } = chat.run;
      // Same freeze mechanism as BACKEND_RUN_COMPLETED — one elapsed-time
      // source of truth, reused rather than a second timer — but a
      // distinct `outcome` ("stopped", never "error"): this was not a
      // backend/transport failure, the user chose to interrupt it.
      const existingTrace = chat.runTraces?.[assistantMessageId];
      const updatedRunTraces = existingTrace
        ? {
            ...chat.runTraces,
            [assistantMessageId]: {
              ...existingTrace,
              // UI PRESENTATION CORRECTION (post-Phase-2) — same merge as
              // BACKEND_RUN_COMPLETED; a user-initiated stop still shows
              // whatever real activity genuinely happened before it.
              steps: mergeActivityTrailIntoRunTraceSteps(activityTrail, existingTrace.steps),
              finalDurationSeconds: elapsedSecondsSince(runStartedAt),
              outcome: "stopped" as const,
            },
          }
        : chat.runTraces;

      // A deliberate stop is not a failure — whatever text had already
      // streamed in (if any) is kept as the message's final content,
      // never overwritten with an error notice. An empty (still-pending)
      // message simply completes empty, same as any other terminal state
      // a message can reach.
      const existingMessage = state.messages[assistantMessageId];
      const nextMessages =
        existingMessage && (existingMessage.status === "pending" || existingMessage.status === "streaming")
          ? { ...state.messages, [assistantMessageId]: { ...existingMessage, status: "complete" as const } }
          : state.messages;

      return {
        ...state,
        chats: { ...state.chats, [chatId]: { ...chat, run: undefined, runTraces: updatedRunTraces } },
        messages: nextMessages,
      };
    }

    // --- Phase 4G: real approval card (approve -> execute lifecycle) ---

    case "APPROVAL_APPROVE_STARTED": {
      const { chatId, proposalId } = action.payload;
      return withMatchingProposal(state, chatId, proposalId, (record) => ({
        ...record,
        approvalCard: { proposalId, phase: "approving" },
      }));
    }

    case "APPROVAL_APPROVE_SUCCEEDED": {
      const { chatId, proposalId, pendingAction } = action.payload;
      return withMatchingProposal(
        state,
        chatId,
        proposalId,
        (record) => ({
          ...record,
          // Authoritative sync (instruction: local approvalCard state is
          // presentation state only) — never left stale after a successful
          // backend transition.
          pendingAction: pendingAction ?? record.pendingAction,
          approvalCard: { proposalId, phase: "executing" },
        }),
        (chat) => ({ ...chat, pendingAction: pendingAction ?? chat.pendingAction }),
      );
    }

    case "APPROVAL_EXECUTE_SUCCEEDED": {
      const { chatId, proposalId, pendingAction, executedAction } = action.payload;
      return withMatchingProposal(
        state,
        chatId,
        proposalId,
        (record) => ({
          ...record,
          pendingAction: pendingAction ?? record.pendingAction,
          approvalCard: { proposalId, phase: "completed", executedAction },
        }),
        (chat) => ({ ...chat, pendingAction: pendingAction ?? chat.pendingAction }),
      );
    }

    case "APPROVAL_REJECT_STARTED": {
      const { chatId, proposalId } = action.payload;
      return withMatchingProposal(state, chatId, proposalId, (record) => ({
        ...record,
        approvalCard: { proposalId, phase: "rejecting" },
      }));
    }

    case "APPROVAL_REJECT_SUCCEEDED": {
      const { chatId, proposalId, pendingAction } = action.payload;
      return withMatchingProposal(
        state,
        chatId,
        proposalId,
        (record) => ({
          ...record,
          pendingAction: pendingAction ?? record.pendingAction,
          // "rejected" is derived straight from pendingAction.status (see
          // deriveApprovalCardView) — no local phase needed for it.
          approvalCard: undefined,
        }),
        (chat) => ({ ...chat, pendingAction: pendingAction ?? chat.pendingAction }),
      );
    }

    case "APPROVAL_REQUEST_FAILED": {
      const { chatId, proposalId, phase, message } = action.payload;
      return withMatchingProposal(state, chatId, proposalId, (record) => ({
        ...record,
        approvalCard: { proposalId, phase, message },
      }));
    }

    case "TOGGLE_ACTION_CARD_COLLAPSED": {
      const { chatId, messageId } = action.payload;
      const chat = state.chats[chatId];
      const record = chat?.actionCards?.[messageId];
      if (!chat || !record) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            actionCards: { ...chat.actionCards, [messageId]: { ...record, collapsed: !record.collapsed } },
          },
        },
      };
    }

    // --- Interaction-capability extension: Teams chat-name selection ---

    case "SELECTION_CHOOSE_STARTED": {
      const { chatId, selectionId } = action.payload;
      return withMatchingSelection(state, chatId, selectionId, (record) => ({
        ...record,
        selectionCard: { selectionId, phase: "choosing" },
      }));
    }

    case "SELECTION_CHOOSE_SUCCEEDED": {
      const { chatId, selectionId, selectedLabel, pendingAction } = action.payload;
      return withMatchingSelection(
        state,
        chatId,
        selectionId,
        (record) => ({
          ...record,
          selectionCard: { selectionId, phase: "resolved", selectedLabel },
        }),
        (chat) => {
          // Resolved: no longer the chat's "current" selection to act on
          // — the choice succeeded, whether it was a read or a write.
          const cleared: Chat = { ...chat, pendingSelection: null, pendingSelectionMessageId: undefined };
          // A WRITE-kind resolution deterministically produced a normal
          // ActionProposal (see selection_service.py's `choose`) —
          // anchor its own new card to the selection's own owning
          // message (there is no live assistant message for this direct
          // API call). A READ-kind resolution carries no `pendingAction`
          // at all; the orchestration layer (chooseSelectionOption)
          // handles re-sending the original request as a new turn.
          return pendingAction
            ? upsertActionCardForProposal(cleared, chat.pendingSelectionMessageId!, pendingAction)
            : cleared;
        },
      );
    }

    case "SELECTION_SKIP_STARTED": {
      const { chatId, selectionId } = action.payload;
      return withMatchingSelection(state, chatId, selectionId, (record) => ({
        ...record,
        selectionCard: { selectionId, phase: "skipping" },
      }));
    }

    case "SELECTION_SKIP_SUCCEEDED": {
      const { chatId, selectionId } = action.payload;
      return withMatchingSelection(
        state,
        chatId,
        selectionId,
        (record) => ({ ...record, selectionCard: { selectionId, phase: "skipped" } }),
        (chat) => ({ ...chat, pendingSelection: null, pendingSelectionMessageId: undefined }),
      );
    }

    case "SELECTION_REQUEST_FAILED": {
      const { chatId, selectionId, message } = action.payload;
      return withMatchingSelection(state, chatId, selectionId, (record) => ({
        ...record,
        selectionCard: { selectionId, phase: "failed", message },
      }));
    }

    case "TOGGLE_SELECTION_CARD_COLLAPSED": {
      const { chatId, messageId } = action.payload;
      const chat = state.chats[chatId];
      const record = chat?.selectionCards?.[messageId];
      if (!chat || !record) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            selectionCards: { ...chat.selectionCards, [messageId]: { ...record, collapsed: !record.collapsed } },
          },
        },
      };
    }

    case "REGENERATE_MESSAGE": {
      const existing = state.messages[action.payload.messageId];
      if (!existing || existing.role !== "assistant") return state;
      return {
        ...state,
        messages: {
          ...state.messages,
          [existing.id]: {
            ...existing,
            text: "",
            status: "pending",
            citations: undefined,
            groundingResult: undefined,
            searchedScope: undefined,
            connectorUnavailable: undefined,
            actionProposalId: undefined,
          },
        },
      };
    }

    case "EDIT_MESSAGE": {
      const { chatId, messageId, text, assistantMessageId, retitle, runToken } = action.payload;
      const chat = state.chats[chatId];
      const message = state.messages[messageId];
      if (!chat || !message) return state;

      const editIndex = chat.messageIds.indexOf(messageId);
      if (editIndex === -1) return state;

      // Editing a message discards it and everything that followed — the
      // conversation continues fresh from the edited text.
      const keptIds = chat.messageIds.slice(0, editIndex);
      const removedIds = chat.messageIds.slice(editIndex);
      const removedIdSet = new Set(removedIds);

      const remainingMessages = { ...state.messages };
      const remainingActionProposals = { ...state.actionProposals };
      for (const id of removedIds) {
        const removed = remainingMessages[id];
        if (removed?.actionProposalId) delete remainingActionProposals[removed.actionProposalId];
        delete remainingMessages[id];
      }

      remainingMessages[messageId] = { ...message, text };
      remainingMessages[assistantMessageId] = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: Date.now(),
      };

      // Phase 4G hardening pass: an action card owned by a message that
      // no longer exists must be discarded along with it — never left as
      // orphaned state referencing a deleted message id (ownership is by
      // stable message id, never by array position, so this is an exact
      // set-membership check, unaffected by any index shift from the
      // truncation above).
      let nextActionCards = chat.actionCards;
      let nextPendingAction = chat.pendingAction;
      let nextPendingActionMessageId = chat.pendingActionMessageId;
      if (chat.actionCards) {
        const survivingEntries = Object.entries(chat.actionCards).filter(([ownerId]) => !removedIdSet.has(ownerId));
        nextActionCards =
          survivingEntries.length === Object.keys(chat.actionCards).length
            ? chat.actionCards
            : Object.fromEntries(survivingEntries);
        if (chat.pendingActionMessageId && removedIdSet.has(chat.pendingActionMessageId)) {
          nextPendingAction = null;
          nextPendingActionMessageId = undefined;
        }
      }

      // Same cleanup, mirrored for selection cards — a discarded
      // PendingSelection (edited/rewound away) must disappear from the
      // active branch, never linger referencing a deleted message.
      let nextSelectionCards = chat.selectionCards;
      let nextPendingSelection = chat.pendingSelection;
      let nextPendingSelectionMessageId = chat.pendingSelectionMessageId;
      let nextPendingSelectionRunToken = chat.pendingSelectionRunToken;
      if (chat.selectionCards) {
        const survivingSelectionEntries = Object.entries(chat.selectionCards).filter(
          ([ownerId]) => !removedIdSet.has(ownerId),
        );
        nextSelectionCards =
          survivingSelectionEntries.length === Object.keys(chat.selectionCards).length
            ? chat.selectionCards
            : Object.fromEntries(survivingSelectionEntries);
        if (chat.pendingSelectionMessageId && removedIdSet.has(chat.pendingSelectionMessageId)) {
          nextPendingSelection = null;
          nextPendingSelectionMessageId = undefined;
          nextPendingSelectionRunToken = undefined;
        }
      }

      // Expandable, sanitized run trace (pre-4H milestone) — same
      // cleanup, mirrored exactly: a discarded assistant turn's trace
      // must disappear from the active branch along with it (instruction
      // section 37 — never replayed, never left dangling on a deleted
      // message id). A surviving prefix message's own trace is untouched.
      let nextRunTraces = chat.runTraces;
      if (chat.runTraces) {
        const survivingTraceEntries = Object.entries(chat.runTraces).filter(([ownerId]) => !removedIdSet.has(ownerId));
        nextRunTraces =
          survivingTraceEntries.length === Object.keys(chat.runTraces).length
            ? chat.runTraces
            : Object.fromEntries(survivingTraceEntries);
      }

      // Pre-4H UX/provenance milestone — same cleanup, mirrored exactly:
      // a discarded assistant turn's Teams source reference must
      // disappear from the active branch along with it.
      let nextSources = chat.sources;
      if (chat.sources) {
        const survivingSourceEntries = Object.entries(chat.sources).filter(([ownerId]) => !removedIdSet.has(ownerId));
        nextSources =
          survivingSourceEntries.length === Object.keys(chat.sources).length
            ? chat.sources
            : Object.fromEntries(survivingSourceEntries);
      }

      // Phase 5.1J correction pass (Part C8) — same cleanup, mirrored
      // exactly for KM source references: a discarded assistant turn's
      // governed-knowledge sources must disappear along with it too,
      // never left dangling on a message no longer part of the active
      // branch.
      let nextKnowledgeSources = chat.knowledgeSources;
      if (chat.knowledgeSources) {
        const survivingKnowledgeSourceEntries = Object.entries(chat.knowledgeSources).filter(
          ([ownerId]) => !removedIdSet.has(ownerId)
        );
        nextKnowledgeSources =
          survivingKnowledgeSourceEntries.length === Object.keys(chat.knowledgeSources).length
            ? chat.knowledgeSources
            : Object.fromEntries(survivingKnowledgeSourceEntries);
      }

      return {
        ...state,
        messages: remainingMessages,
        actionProposals: remainingActionProposals,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            title: retitle ? deriveChatTitle(text) : chat.title,
            demoRun: retitle ? undefined : chat.demoRun,
            messageIds: [...keptIds, messageId, assistantMessageId],
            actionCards: nextActionCards,
            pendingAction: nextPendingAction,
            pendingActionMessageId: nextPendingActionMessageId,
            selectionCards: nextSelectionCards,
            pendingSelection: nextPendingSelection,
            pendingSelectionMessageId: nextPendingSelectionMessageId,
            pendingSelectionRunToken: nextPendingSelectionRunToken,
            runTraces: nextRunTraces,
            sources: nextSources,
            knowledgeSources: nextKnowledgeSources,
            // Backend-sourced chats: seed a fresh run exactly like
            // SEND_MESSAGE does, so the truncated chat immediately shows
            // the new assistant placeholder as in-flight.
            ...(runToken
              ? {
                  run: { runToken, assistantMessageId, currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
                  runTraces: { ...nextRunTraces, [assistantMessageId]: { steps: [], expanded: false } },
                }
              : null),
          },
        },
      };
    }

    case "NEW_CHAT":
      return {
        ...state,
        activeChatId: null,
        workspaceScope: { type: "general" },
        mainView: "workspace",
        draft: freshDraft(),
      };

    case "NUDGE_COMPOSER":
      return { ...state, composerNudgeAt: action.payload.timestamp };

    case "SELECT_CHAT": {
      const chat = state.chats[action.payload.chatId];
      const nextScope: WorkspaceScope = chat?.projectId
        ? { type: "project", projectId: chat.projectId }
        : { type: "general" };
      return {
        ...state,
        activeChatId: action.payload.chatId,
        workspaceScope: nextScope,
        mainView: "workspace",
        // Opening a chat marks it read, same as any normal inbox.
        chats: chat?.unread
          ? { ...state.chats, [chat.id]: { ...chat, unread: false } }
          : state.chats,
        draft: { ...state.draft, text: "", attachments: [] },
      };
    }

    case "SET_DRAFT_TEXT":
      return { ...state, draft: { ...state.draft, text: action.payload.text } };

    case "SET_THINKING_EFFORT": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, thinkingEffort: action.payload.value },
          },
        };
      }
      return { ...state, draft: { ...state.draft, thinkingEffort: action.payload.value } };
    }

    case "SET_SKILL": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, activeSkillId: action.payload.skillId },
          },
        };
      }
      return { ...state, draft: { ...state.draft, skillId: action.payload.skillId } };
    }

    case "SET_MODEL": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, selectedModelId: action.payload.modelId },
          },
        };
      }
      return { ...state, draft: { ...state.draft, modelId: action.payload.modelId } };
    }

    case "TOGGLE_PIN": {
      const chat = state.chats[action.payload.chatId];
      if (!chat) return state;
      const nextPinned = !chat.pinned;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chat.id]: {
            ...chat,
            pinned: nextPinned,
            pinnedAt: nextPinned ? action.payload.timestamp : undefined,
          },
        },
      };
    }

    case "TOGGLE_UNREAD": {
      const chat = state.chats[action.payload.chatId];
      if (!chat) return state;
      return {
        ...state,
        chats: { ...state.chats, [chat.id]: { ...chat, unread: !chat.unread } },
      };
    }

    case "RENAME_CHAT": {
      const chat = state.chats[action.payload.chatId];
      const title = action.payload.title.trim();
      if (!chat || !title) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chat.id]: { ...chat, title },
        },
      };
    }

    case "DELETE_CHAT": {
      const { chatId } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;

      const remainingChats = { ...state.chats };
      delete remainingChats[chatId];

      const remainingMessages = { ...state.messages };
      // Drop each message's action proposal too, the same way EDIT_MESSAGE
      // does — otherwise deleting a chat leaves its proposals orphaned in
      // state forever, referenced by nothing.
      const remainingActionProposals = { ...state.actionProposals };
      for (const messageId of chat.messageIds) {
        const removed = remainingMessages[messageId];
        if (removed?.actionProposalId) delete remainingActionProposals[removed.actionProposalId];
        delete remainingMessages[messageId];
      }

      const wasActive = state.activeChatId === chatId;

      // If a task owned this chat, unlink it and drop its run history — every
      // run points at a messageId that no longer exists. The next Run now
      // starts a fresh chat.
      const nextScheduledTasks = { ...state.scheduledTasks };
      for (const task of Object.values(nextScheduledTasks)) {
        if (task.chatId === chatId) {
          nextScheduledTasks[task.id] = { ...task, chatId: undefined, runs: [] };
        }
      }

      return {
        ...state,
        chats: remainingChats,
        chatOrder: state.chatOrder.filter((id) => id !== chatId),
        messages: remainingMessages,
        actionProposals: remainingActionProposals,
        scheduledTasks: nextScheduledTasks,
        activeChatId: wasActive ? null : state.activeChatId,
        draft: wasActive ? freshDraft() : state.draft,
      };
    }

    case "TOGGLE_CONNECTOR": {
      const id = action.payload.connectorId;
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        const has = chat.connectorIds.includes(id);
        const connectorIds = has
          ? chat.connectorIds.filter((c) => c !== id)
          : [...chat.connectorIds, id];
        return {
          ...state,
          chats: { ...state.chats, [chat.id]: { ...chat, connectorIds } },
        };
      }
      const has = state.draft.connectorIds.includes(id);
      const connectorIds = has
        ? state.draft.connectorIds.filter((c) => c !== id)
        : [...state.draft.connectorIds, id];
      return { ...state, draft: { ...state.draft, connectorIds } };
    }

    case "ADD_ATTACHMENTS":
      return {
        ...state,
        draft: {
          ...state.draft,
          attachments: [...state.draft.attachments, ...action.payload.attachments],
        },
      };

    case "REMOVE_ATTACHMENT":
      return {
        ...state,
        draft: {
          ...state.draft,
          attachments: state.draft.attachments.filter((a) => a.id !== action.payload.id),
        },
      };

    case "UPDATE_DRAFT_IMAGE_ATTACHMENT": {
      const { id, patch } = action.payload;
      return {
        ...state,
        draft: {
          ...state.draft,
          attachments: state.draft.attachments.map((a) =>
            a.id === id && a.kind === "image" ? { ...a, ...patch } : a,
          ),
        },
      };
    }

    case "SET_ATTACHMENT_LIMIT_NOTICE":
      return {
        ...state,
        draft: {
          ...state.draft,
          attachmentLimitNotice: { message: action.payload.message, key: action.payload.key },
        },
      };

    case "CLEAR_ATTACHMENT_LIMIT_NOTICE":
      if (state.draft.attachmentLimitNotice === null) return state;
      return { ...state, draft: { ...state.draft, attachmentLimitNotice: null } };

    case "CREATE_DRAFT_CHAT": {
      const { chatId, timestamp } = action.payload;
      // Idempotent -- a second CREATE_DRAFT_CHAT for the same id (e.g. a
      // caller that raced ensureActiveChat twice) is a safe no-op.
      if (state.chats[chatId]) return state;
      const chat: Chat = {
        id: chatId,
        title: "New chat",
        projectId: state.workspaceScope.type === "project" ? state.workspaceScope.projectId : null,
        pinned: false,
        createdAt: timestamp,
        messageIds: [],
        activeSkillId: state.draft.skillId,
        connectorIds: state.draft.connectorIds,
        selectedModelId: state.draft.modelId,
        thinkingEffort: state.draft.thinkingEffort,
      };
      return {
        ...state,
        chats: { ...state.chats, [chatId]: chat },
        chatOrder: [chatId, ...state.chatOrder],
        activeChatId: chatId,
      };
    }

    case "BACKEND_SESSION_ENSURED": {
      const { chatId, sessionId } = action.payload;
      const chat = state.chats[chatId];
      if (!chat || chat.backendSessionId) return state;
      return { ...state, chats: { ...state.chats, [chatId]: { ...chat, backendSessionId: sessionId } } };
    }

    case "HYDRATE_SAVED_SESSIONS": {
      // Dedupe key is ALWAYS backendSessionId, never Chat.id equality —
      // instruction: a locally-created chat that already owns this exact
      // backend session (e.g. resumed earlier this session) must be
      // reconciled, not duplicated, and this same dispatch must be safe to
      // run more than once (StrictMode double-invocation of the boot
      // effect) without ever inserting a second copy or re-ordering an
      // already-hydrated chat.
      const existingByBackendId = new Map<string, string>();
      for (const chat of Object.values(state.chats)) {
        if (chat.backendSessionId) existingByBackendId.set(chat.backendSessionId, chat.id);
      }

      let chats = state.chats;
      let changed = false;
      const newIds: string[] = [];

      for (const summary of action.payload.sessions) {
        const existingId = existingByBackendId.get(summary.session_id);
        if (existingId) {
          const existing = chats[existingId];
          // Only the title is server-authoritative to reconcile here — a
          // chat's active run/local messages/draft/mock-specific state are
          // never touched by hydration.
          if (existing.title !== summary.title) {
            if (!changed) chats = { ...chats };
            changed = true;
            chats[existingId] = { ...existing, title: summary.title };
          }
          continue;
        }

        if (!changed) chats = { ...chats };
        changed = true;
        const createdAt = Date.parse(summary.updated_at);
        const chat: Chat = {
          id: summary.session_id,
          title: summary.title,
          projectId: null,
          pinned: false,
          createdAt: Number.isFinite(createdAt) ? createdAt : Date.now(),
          messageIds: [],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: DEFAULT_MODEL_ID,
          thinkingEffort: DEFAULT_THINKING_EFFORT,
          backendSessionId: summary.session_id,
          historyHydrationStatus: "unloaded",
        };
        chats[summary.session_id] = chat;
        newIds.push(summary.session_id);
      }

      // GET /api/sessions already arrives newest-activity-first — appended
      // after whatever chatOrder already holds (never reordering existing
      // entries) so that backend order is preserved for the newly hydrated
      // chats. activeChatId is deliberately left untouched (boot must never
      // auto-open a conversation). A successful response — including one
      // with ZERO sessions — always settles `savedChatsHydrationStatus` to
      // "loaded", which is what lets the sidebar tell a genuinely empty
      // saved-chat list apart from "still loading"/"failed to load".
      const merged = !changed ? state : newIds.length > 0 ? { ...state, chats, chatOrder: [...state.chatOrder, ...newIds] } : { ...state, chats };
      return { ...merged, savedChatsHydrationStatus: "loaded", savedChatsHydrationError: undefined };
    }

    case "SAVED_CHATS_HYDRATION_STARTED":
      return { ...state, savedChatsHydrationStatus: "loading", savedChatsHydrationError: undefined };

    case "SAVED_CHATS_HYDRATION_FAILED":
      // Never touches `chats`/`chatOrder` — a failed (re)fetch must never
      // erase whatever local/already-hydrated chats already exist.
      return { ...state, savedChatsHydrationStatus: "error", savedChatsHydrationError: action.payload.message };

    case "HISTORY_FETCH_STARTED": {
      const chat = state.chats[action.payload.chatId];
      if (!chat) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [action.payload.chatId]: { ...chat, historyHydrationStatus: "loading", historyError: undefined },
        },
      };
    }

    case "HISTORY_FETCH_SUCCEEDED": {
      const { chatId, response } = action.payload;
      const chat = state.chats[chatId];
      // Chat removed locally (DELETE_CHAT) while the request was in
      // flight — a late response must never resurrect it.
      if (!chat) return state;
      // Superseded (a retry or a second effect run already moved this
      // chat past "loading") — never apply a stale result on top.
      if (chat.historyHydrationStatus !== "loading") return state;
      // Defensive correctness guard: if a genuine local turn already
      // appended real messages to this chat while its history was still
      // loading (e.g. the user sent a message before the fetch resolved),
      // a late historical transcript must never clobber it — just mark
      // the fetch as settled without touching messageIds/messages.
      if (chat.messageIds.length > 0) {
        return { ...state, chats: { ...state.chats, [chatId]: { ...chat, historyHydrationStatus: "loaded" } } };
      }

      const messages: Record<string, Message> = {};
      const messageIds: string[] = [];
      // B7 corrective pass — durable, turn-owned Source/Knowledge-source
      // provenance, reprojected through history exactly like the live SSE
      // `BACKEND_MESSAGE_COMPLETED` case above builds `chat.sources`/
      // `chat.knowledgeSources`: keyed by message id, present only when
      // that turn actually produced evidence. Message.tsx already reads
      // from these two chat-level maps (never from `Message` itself), so
      // populating them here is sufficient to converge live and hydrated
      // rendering on the exact same SourceChip path.
      const sources: Record<string, SourceReferenceDTO> = {};
      const knowledgeSources: Record<string, KnowledgeSourceReferenceDTO[]> = {};
      for (const dto of response.messages) {
        messageIds.push(dto.message_id);
        if (dto.source) sources[dto.message_id] = dto.source;
        if (dto.knowledge_sources && dto.knowledge_sources.length > 0)
          knowledgeSources[dto.message_id] = dto.knowledge_sources;
        messages[dto.message_id] = {
          id: dto.message_id,
          chatId,
          role: dto.role,
          text: dto.text,
          status: "complete",
          // Caller (the boot/lazy-load effect below) already validated
          // every dto.created_at parses to a finite timestamp before ever
          // dispatching this action — see that effect's own comment for
          // why a malformed one fails the whole request instead of
          // reaching here with an invented fallback time.
          createdAt: Date.parse(dto.created_at),
          // POST-5.1 B4D — durable, server-owned image references, mapped
          // verbatim in server order into the DEDICATED `persistedAttachments`
          // field (never the existing mock/file/folder `attachments` field,
          // and never a File/Blob/objectUrl — metadata only). The backend
          // already guarantees only LINKED attachments on the owning USER
          // message ever appear here (assistant messages always get `[]`) —
          // never re-derived/re-filtered on this side. `undefined` (not an
          // empty array) when there are none, matching every other optional
          // Message field's own "absent means none" convention.
          persistedAttachments:
            dto.attachments.length > 0
              ? dto.attachments.map((a) => ({
                  attachmentId: a.attachment_id,
                  filename: a.filename,
                  mimeType: a.mime_type,
                  sizeBytes: a.size_bytes,
                }))
              : undefined,
        };
      }

      return {
        ...state,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            messageIds,
            historyHydrationStatus: "loaded",
            historyError: undefined,
            ...(Object.keys(sources).length > 0 ? { sources: { ...chat.sources, ...sources } } : null),
            ...(Object.keys(knowledgeSources).length > 0
              ? { knowledgeSources: { ...chat.knowledgeSources, ...knowledgeSources } }
              : null),
          },
        },
        messages: { ...state.messages, ...messages },
      };
    }

    case "HISTORY_FETCH_FAILED": {
      const { chatId, message } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;
      if (chat.historyHydrationStatus !== "loading") return state;
      return {
        ...state,
        chats: { ...state.chats, [chatId]: { ...chat, historyHydrationStatus: "error", historyError: message } },
      };
    }

    case "RETRY_HISTORY_LOAD": {
      const chat = state.chats[action.payload.chatId];
      if (!chat || chat.historyHydrationStatus !== "error") return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [action.payload.chatId]: { ...chat, historyHydrationStatus: "unloaded", historyError: undefined },
        },
      };
    }

    case "TOGGLE_SIDEBAR":
      return { ...state, sidebarCollapsed: !state.sidebarCollapsed };

    case "CREATE_PROJECT": {
      const { projectId, name, description, timestamp } = action.payload;
      const project: Project = {
        id: projectId,
        name,
        description,
        instructionsText: "",
        connectorIds: [],
        fileIds: [],
        createdAt: timestamp,
      };
      return {
        ...state,
        projects: { ...state.projects, [projectId]: project },
        projectOrder: [projectId, ...state.projectOrder],
        workspaceScope: { type: "project", projectId },
        activeChatId: null,
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "ENTER_PROJECT": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      return {
        ...state,
        workspaceScope: { type: "project", projectId: project.id },
        activeChatId: null,
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "SET_PROJECT_INSTRUCTIONS": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: { ...project, instructionsText: action.payload.text },
        },
      };
    }

    case "RENAME_PROJECT": {
      const project = state.projects[action.payload.projectId];
      const name = action.payload.name.trim();
      if (!project || !name) return state;
      const description = action.payload.description?.trim();
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: { ...project, name, description: description || undefined },
        },
      };
    }

    case "TOGGLE_PROJECT_PINNED": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const nextPinned = !project.pinned;
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: {
            ...project,
            pinned: nextPinned,
            pinnedAt: nextPinned ? action.payload.timestamp : undefined,
          },
        },
      };
    }

    case "DELETE_PROJECT": {
      const { projectId } = action.payload;
      const project = state.projects[projectId];
      if (!project) return state;

      const remainingProjects = { ...state.projects };
      delete remainingProjects[projectId];

      const remainingProjectFiles = { ...state.projectFiles };
      for (const fileId of project.fileIds) {
        delete remainingProjectFiles[fileId];
      }

      const remainingScheduledTasks = Object.fromEntries(
        Object.entries(state.scheduledTasks).filter(([, task]) => task.projectId !== projectId),
      );

      const updatedChats = { ...state.chats };
      for (const chat of Object.values(updatedChats)) {
        if (chat.projectId === projectId) {
          updatedChats[chat.id] = { ...chat, projectId: null };
        }
      }

      // Deleting a project also deletes its scheduled tasks — unlink their
      // chats too. Without this a chat is filtered out of Chats (still
      // flagged as task-owned) while its task row is gone, so it disappears
      // from the sidebar entirely.
      for (const task of Object.values(state.scheduledTasks)) {
        if (task.projectId === projectId && task.chatId && updatedChats[task.chatId]) {
          const { scheduledTaskId: _unlinked, ...rest } = updatedChats[task.chatId];
          updatedChats[task.chatId] = rest;
        }
      }

      const wasActiveWorkspace =
        state.workspaceScope.type === "project" && state.workspaceScope.projectId === projectId;

      // Deleting a project also deletes its scheduled tasks — if one of those
      // was open on the detail page, that page would be left showing a
      // "no longer exists" dead end, so fall back to the task list.
      const viewedTaskRemoved =
        state.mainView === "scheduledTaskDetail" &&
        state.activeScheduledTaskId !== null &&
        !remainingScheduledTasks[state.activeScheduledTaskId];

      return {
        ...state,
        projects: remainingProjects,
        projectOrder: state.projectOrder.filter((id) => id !== projectId),
        projectFiles: remainingProjectFiles,
        scheduledTasks: remainingScheduledTasks,
        chats: updatedChats,
        workspaceScope: wasActiveWorkspace ? { type: "general" } : state.workspaceScope,
        mainView: viewedTaskRemoved ? "scheduledTasks" : state.mainView,
      };
    }

    case "TOGGLE_PROJECT_CONNECTOR": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const id = action.payload.connectorId;
      const has = project.connectorIds.includes(id);
      const connectorIds = has
        ? project.connectorIds.filter((c) => c !== id)
        : [...project.connectorIds, id];
      return {
        ...state,
        projects: { ...state.projects, [project.id]: { ...project, connectorIds } },
      };
    }

    case "ADD_PROJECT_FILES": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const nextFiles = { ...state.projectFiles };
      const newIds: string[] = [];
      for (const file of action.payload.files) {
        nextFiles[file.id] = file;
        newIds.push(file.id);
      }
      return {
        ...state,
        projectFiles: nextFiles,
        projects: {
          ...state.projects,
          [project.id]: { ...project, fileIds: [...project.fileIds, ...newIds] },
        },
      };
    }

    case "REMOVE_PROJECT_FILE": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const { fileId } = action.payload;

      const remainingProjectFiles = { ...state.projectFiles };
      delete remainingProjectFiles[fileId];

      return {
        ...state,
        projectFiles: remainingProjectFiles,
        projects: {
          ...state.projects,
          [project.id]: { ...project, fileIds: project.fileIds.filter((id) => id !== fileId) },
        },
      };
    }

    case "START_TASK_RUN": {
      const { taskId, runId, chatId, messageId, speakerLabel, timestamp, focus } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;

      // Resolve the chat from LIVE state, not the payload. The caller closes
      // over a snapshot of state, so two quick clicks would both arrive
      // carrying a freshly minted chatId and create two chats for one task.
      const existingChat = task.chatId ? state.chats[task.chatId] : undefined;
      const targetChatId = existingChat ? existingChat.id : chatId;

      const nextChats = { ...state.chats };
      let nextChatOrder = state.chatOrder;

      if (existingChat) {
        nextChats[targetChatId] = {
          ...existingChat,
          messageIds: [...existingChat.messageIds, messageId],
          unread: focus ? false : state.activeChatId !== targetChatId,
        };
      } else {
        nextChats[targetChatId] = {
          id: targetChatId,
          title: task.name,
          projectId: task.projectId ?? null,
          scheduledTaskId: task.id,
          pinned: false,
          createdAt: timestamp,
          messageIds: [messageId],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: task.modelId,
          thinkingEffort: DEFAULT_THINKING_EFFORT,
          unread: !focus,
        };
        nextChatOrder = [targetChatId, ...state.chatOrder];
      }

      const run: TaskRun = {
        id: runId,
        startedAt: timestamp,
        status: "running",
        messageId,
        trigger: "manual",
      };

      return {
        ...state,
        chats: nextChats,
        chatOrder: nextChatOrder,
        messages: {
          ...state.messages,
          [messageId]: {
            id: messageId,
            chatId: targetChatId,
            role: "assistant",
            text: "",
            status: "pending",
            speakerLabel,
            createdAt: timestamp,
          },
        },
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: { ...task, chatId: targetChatId, runs: [...task.runs, run] },
        },
        ...(focus
          ? {
              activeChatId: targetChatId,
              mainView: "workspace" as const,
              workspaceScope: task.projectId
                ? ({ type: "project", projectId: task.projectId } as WorkspaceScope)
                : ({ type: "general" } as WorkspaceScope),
              draft: freshDraft(),
            }
          : {}),
      };
    }

    case "COMPLETE_TASK_RUN": {
      const { taskId, runId, summary, skippedConnectorIds } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;
      return {
        ...state,
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: {
            ...task,
            runs: task.runs.map((run) =>
              run.id === runId
                ? { ...run, status: "completed" as const, summary, skippedConnectorIds }
                : run,
            ),
          },
        },
      };
    }

    case "SEED_TASK_RUN": {
      const {
        taskId, runId, chatId, messageId, text, summary, speakerLabel, ranAt, suggestedConnectorIds,
      } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;

      const existingChat = task.chatId ? state.chats[task.chatId] : undefined;
      const targetChatId = existingChat ? existingChat.id : chatId;

      const nextChats = { ...state.chats };
      let nextChatOrder = state.chatOrder;

      if (existingChat) {
        nextChats[targetChatId] = {
          ...existingChat,
          messageIds: [...existingChat.messageIds, messageId],
          unread: state.activeChatId !== targetChatId,
        };
      } else {
        nextChats[targetChatId] = {
          id: targetChatId,
          title: task.name,
          projectId: task.projectId ?? null,
          scheduledTaskId: task.id,
          pinned: false,
          createdAt: ranAt,
          messageIds: [messageId],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: task.modelId,
          thinkingEffort: DEFAULT_THINKING_EFFORT,
          unread: true,
        };
        nextChatOrder = [targetChatId, ...state.chatOrder];
      }

      return {
        ...state,
        chats: nextChats,
        chatOrder: nextChatOrder,
        // Born complete: useProgressiveReveal only animates on a
        // pending -> complete transition, so history renders instantly.
        messages: {
          ...state.messages,
          [messageId]: {
            id: messageId,
            chatId: targetChatId,
            role: "assistant",
            text,
            status: "complete",
            speakerLabel,
            suggestedConnectorIds,
            createdAt: ranAt,
          },
        },
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: {
            ...task,
            chatId: targetChatId,
            runs: [
              ...task.runs,
              { id: runId, startedAt: ranAt, status: "completed", messageId, summary, trigger: "seed" },
            ],
          },
        },
      };
    }

    case "PROPOSE_DISTRIBUTION": {
      const { proposalId, messageId, connectorId, destinationLabel, body } = action.payload;
      const message = state.messages[messageId];
      if (!message) return state;
      return {
        ...state,
        messages: {
          ...state.messages,
          [messageId]: { ...message, distributionProposalId: proposalId },
        },
        actionProposals: {
          ...state.actionProposals,
          [proposalId]: {
            id: proposalId,
            chatId: message.chatId,
            connectorId,
            actionType: "post_to_channel",
            title: `Post to ${destinationLabel}`,
            fields: [
              { label: "Destination", value: destinationLabel },
              { label: "Content", value: "This response" },
            ],
            body,
            status: "pending",
          },
        },
      };
    }

    case "ADD_DRAFT_SOURCE": {
      const { source } = action.payload;
      if (state.draft.sources.some((s) => s.kind === source.kind && s.scope === source.scope)) {
        return state;
      }
      return { ...state, draft: { ...state.draft, sources: [...state.draft.sources, source] } };
    }

    case "REMOVE_DRAFT_SOURCE":
      return {
        ...state,
        draft: {
          ...state.draft,
          sources: state.draft.sources.filter((s) => s.id !== action.payload.id),
        },
      };

    case "CREATE_SCHEDULED_TASK": {
      const task = action.payload;
      if (task.projectId && !state.projects[task.projectId]) return state;

      // Adopt the setup conversation as this task's output chat, so one
      // scheduling conversation yields exactly one sidebar row rather than a
      // chat and a task that look identical.
      const nextChats = { ...state.chats };
      const linked = task.chatId ? state.chats[task.chatId] : undefined;
      if (linked) {
        nextChats[linked.id] = {
          ...linked,
          scheduledTaskId: task.id,
          title: linked.title === "New scheduled task" ? task.name : linked.title,
        };
      }

      return {
        ...state,
        chats: nextChats,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: task },
      };
    }

    case "UPDATE_SCHEDULED_TASK": {
      const task = action.payload;
      const previous = state.scheduledTasks[task.id];
      if (!previous) return state;

      // Keep the owned chat in step with the task, but never overwrite a
      // title the user set themselves.
      const nextChats = { ...state.chats };
      const linked = task.chatId ? state.chats[task.chatId] : undefined;
      if (linked) {
        nextChats[linked.id] = {
          ...linked,
          title: linked.title === previous.name ? task.name : linked.title,
          projectId: task.projectId ?? null,
        };
      }

      return {
        ...state,
        chats: nextChats,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: task },
      };
    }

    case "DELETE_SCHEDULED_TASK": {
      const { id } = action.payload;
      const task = state.scheduledTasks[id];
      if (!task) return state;
      const remainingScheduledTasks = { ...state.scheduledTasks };
      delete remainingScheduledTasks[id];

      // Keep the chat and its past briefs — unlinking drops it back under
      // Chats. Silently destroying a conversation behind a "delete task"
      // confirmation would be a nasty surprise.
      const nextChats = { ...state.chats };
      if (task.chatId && nextChats[task.chatId]) {
        const { scheduledTaskId: _unlinked, ...rest } = nextChats[task.chatId];
        nextChats[task.chatId] = rest;
      }

      const wasViewingIt = state.mainView === "scheduledTaskDetail" && state.activeScheduledTaskId === id;
      return {
        ...state,
        chats: nextChats,
        scheduledTasks: remainingScheduledTasks,
        mainView: wasViewingIt ? "scheduledTasks" : state.mainView,
      };
    }

    case "TOGGLE_SCHEDULED_TASK_ACTIVE": {
      const task = state.scheduledTasks[action.payload.id];
      if (!task) return state;
      return {
        ...state,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: { ...task, active: !task.active } },
      };
    }

    case "OPEN_ALL_PROJECTS":
      return { ...state, mainView: "allProjects" };

    case "CLOSE_ALL_PROJECTS":
      return { ...state, mainView: "workspace" };

    case "OPEN_SCHEDULED_TASKS":
      return { ...state, mainView: "scheduledTasks" };

    case "VIEW_SCHEDULED_TASK":
      return { ...state, mainView: "scheduledTaskDetail", activeScheduledTaskId: action.payload.id };

    case "MOVE_CHAT_TO_PROJECT": {
      const { chatId, projectId, timestamp } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;
      const updatedChat: Chat = {
        ...chat,
        projectId,
        pinnedAt: chat.pinned ? timestamp : chat.pinnedAt,
      };
      const isActive = state.activeChatId === chatId;
      return {
        ...state,
        chats: { ...state.chats, [chatId]: updatedChat },
        workspaceScope: isActive
          ? projectId
            ? { type: "project", projectId }
            : { type: "general" }
          : state.workspaceScope,
      };
    }

    case "CONNECT_CONNECTOR": {
      const connector = state.connectors[action.payload.connectorId];
      if (!connector) return state;
      return {
        ...state,
        connectors: {
          ...state.connectors,
          [connector.id]: { ...connector, state: "connected" },
        },
      };
    }

    case "DISCONNECT_CONNECTOR": {
      const connector = state.connectors[action.payload.connectorId];
      if (!connector) return state;
      return {
        ...state,
        connectors: {
          ...state.connectors,
          [connector.id]: { ...connector, state: "not_connected" },
        },
      };
    }

    case "CREATE_SKILL": {
      const { skillId, name, description, instructions } = action.payload;
      const skill: Skill = { id: skillId, name, description, instructions };
      return {
        ...state,
        skills: { ...state.skills, [skillId]: skill },
        skillOrder: [skillId, ...state.skillOrder],
      };
    }

    case "UPDATE_SKILL": {
      const { skillId, name, description, instructions } = action.payload;
      const existing = state.skills[skillId];
      if (!existing) return state;
      return {
        ...state,
        skills: {
          ...state.skills,
          [skillId]: { ...existing, name, description, instructions },
        },
      };
    }

    case "DELETE_SKILL": {
      const { skillId } = action.payload;
      if (!state.skills[skillId]) return state;

      const remainingSkills = { ...state.skills };
      delete remainingSkills[skillId];

      const updatedChats = { ...state.chats };
      for (const chat of Object.values(updatedChats)) {
        if (chat.activeSkillId === skillId) {
          updatedChats[chat.id] = { ...chat, activeSkillId: null };
        }
      }

      return {
        ...state,
        skills: remainingSkills,
        skillOrder: state.skillOrder.filter((id) => id !== skillId),
        chats: updatedChats,
        draft: state.draft.skillId === skillId ? { ...state.draft, skillId: null } : state.draft,
      };
    }

    case "SET_ACTION_PROPOSAL_STATUS": {
      const proposal = state.actionProposals[action.payload.id];
      if (!proposal) return state;
      return {
        ...state,
        actionProposals: {
          ...state.actionProposals,
          [proposal.id]: {
            ...proposal,
            status: action.payload.status,
            createdTaskId: action.payload.createdTaskId ?? proposal.createdTaskId,
          },
        },
      };
    }

    case "OPEN_SETTINGS":
      return {
        ...state,
        settingsModal: {
          open: true,
          section: action.payload.section ?? state.settingsModal.section,
        },
      };

    case "CLOSE_SETTINGS":
      return { ...state, settingsModal: { ...state.settingsModal, open: false } };

    case "SET_SETTINGS_SECTION":
      return {
        ...state,
        settingsModal: { ...state.settingsModal, section: action.payload.section },
      };

    case "OPEN_PROJECT_SETTINGS":
      return {
        ...state,
        projectSettingsModal: {
          open: true,
          section: action.payload.section ?? state.projectSettingsModal.section,
        },
      };

    case "CLOSE_PROJECT_SETTINGS":
      return {
        ...state,
        projectSettingsModal: { ...state.projectSettingsModal, open: false },
      };

    case "SET_PROJECT_SETTINGS_SECTION":
      return {
        ...state,
        projectSettingsModal: { ...state.projectSettingsModal, section: action.payload.section },
      };

    default:
      return state;
  }
}

interface AppContextValue {
  state: AppState;
  activeChat: Chat | null;
  activeMessages: Message[];
  activeSkillId: string | null;
  activeConnectorIds: string[];
  activeModelId: string;
  activeThinkingEffort: ThinkingEffort;
  chatList: Chat[];
  projects: Project[];
  activeProject: Project | null;
  connectorList: Connector[];
  skillList: Skill[];
  sendMessage: (overrideText?: string) => void;
  startScheduledTaskSetup: (options?: { seedPrompt?: string }) => void;
  /** Phase 4G — approves the given proposal (which must still be the
   * chat's current backend-active `pendingAction.proposal_id` — a stale
   * click from a superseded historical card is a safe no-op, mirroring
   * the reducer's own `withMatchingProposal` guard), then immediately
   * attempts to execute it. Named distinctly from the pre-existing mock
   * `approveAction`/`cancelAction` pair (a different, older system
   * operating on `state.actionProposals`) to avoid any collision. Takes
   * only `chatId`/`proposalId` — both structured identifiers, no
   * free-text parameter exists anywhere in this path. */
  approvePendingAction: (chatId: string, proposalId: string) => void;
  rejectPendingAction: (chatId: string, proposalId: string) => void;
  /** Phase 4G hardening pass — toggles one action card's `collapsed` flag
   * in place. Never moves, edits, or removes the card; never timer-driven. */
  toggleActionCardCollapsed: (chatId: string, messageId: string) => void;
  /** Interaction-capability extension — chooses one candidate for the
   * chat's current active Teams chat selection (must still be
   * `chatId`'s `pendingSelection.selection_id` — a stale click is a safe
   * no-op, mirroring `approvePendingAction`). NOT approval: for a
   * pending write, this only produces a normal `ActionProposal` — a
   * separate `ApprovalCard` then requires its own explicit Approve. */
  chooseSelectionOption: (chatId: string, selectionId: string, optionId: string) => void;
  /** Skips the chat's current active Teams chat selection — no candidate
   * is chosen, nothing is sent, no proposal is created. */
  skipSelectionOption: (chatId: string, selectionId: string) => void;
  /** Toggles one selection card's `collapsed` flag — mirrors
   * `toggleActionCardCollapsed`. */
  toggleSelectionCardCollapsed: (chatId: string, messageId: string) => void;
  /** Expandable, sanitized run trace (pre-4H milestone) — toggles one
   * run trace's local `expanded` flag in place. Purely local presentation
   * state: never calls the backend, never reruns anything (instruction
   * section 35). */
  toggleRunTraceExpanded: (chatId: string, messageId: string) => void;
  /** Pre-4H refinement — the composer's Stop control. Aborts the active
   * run's client transport and marks it stopped locally; see
   * `stopActiveRun`'s own docstring for what this can/cannot guarantee. */
  stopActiveRun: (chatId: string) => void;
  regenerateMessage: (messageId: string) => void;
  editMessage: (messageId: string, newText: string) => void;
  newChat: () => void;
  selectChat: (chatId: string) => void;
  setDraftText: (text: string) => void;
  setThinkingEffort: (value: ThinkingEffort) => void;
  setSkill: (skillId: string | null) => void;
  setModel: (modelId: string) => void;
  togglePin: (chatId: string) => void;
  toggleUnread: (chatId: string) => void;
  renameChat: (chatId: string, title: string) => void;
  deleteChat: (chatId: string) => void;
  /** POST-5.1 B4C — retries a failed `GET /api/sessions/{id}/history` load
   * for a saved chat (`historyHydrationStatus === "error"` only; a no-op
   * otherwise). See AppState.tsx's lazy-history-load effect, which resumes
   * fetching the moment this flips the chat back to `"unloaded"`. */
  retryHistoryLoad: (chatId: string) => void;
  /** POST-5.1 B4C correction pass — retries a failed GLOBAL saved-chat-list
   * load (`state.savedChatsHydrationStatus === "error"`). Safe to call
   * regardless of current status — always issues a fresh `GET
   * /api/sessions` and re-merges via the same idempotent reducer path
   * boot itself uses. */
  retrySavedChats: () => void;
  toggleConnector: (connectorId: string) => void;
  addAttachments: (attachments: Attachment[]) => void;
  removeAttachment: (id: string) => void;
  /** POST-5.1 B3 — the one central image-ingestion entry point (picker,
   * paste, and drag/drop all call this). See AppState.tsx's own
   * `queueImageFiles` docstring for the full eligibility/preflight/
   * single-flight-session semantics. */
  queueImageFiles: (files: File[]) => void;
  retryImageAttachment: (draftId: string) => void;
  toggleSidebar: () => void;
  createProject: (name: string, description?: string) => void;
  enterProject: (projectId: string) => void;
  setProjectInstructions: (projectId: string, text: string) => void;
  renameProject: (projectId: string, name: string, description?: string) => void;
  deleteProject: (projectId: string) => void;
  toggleProjectPinned: (projectId: string) => void;
  toggleProjectConnector: (projectId: string, connectorId: string) => void;
  addProjectFiles: (projectId: string, files: ProjectFile[]) => void;
  removeProjectFile: (projectId: string, fileId: string) => void;
  updateScheduledTask: (task: ScheduledTask) => void;
  runScheduledTask: (taskId: string, options?: { focus?: boolean }) => void;
  addDraftSource: (source: TaskSource) => void;
  removeDraftSource: (id: string) => void;
  proposeDistribution: (messageId: string, destinationId: string) => void;
  deleteScheduledTask: (id: string) => void;
  toggleScheduledTaskActive: (id: string) => void;
  openAllProjects: () => void;
  closeAllProjects: () => void;
  openScheduledTasks: () => void;
  viewScheduledTask: (id: string) => void;
  moveChatToProject: (chatId: string, projectId: string | null) => void;
  connectConnector: (connectorId: string) => void;
  disconnectConnector: (connectorId: string) => void;
  createSkill: (input: { name: string; description: string; instructions: string }) => void;
  updateSkill: (
    skillId: string,
    input: { name: string; description: string; instructions: string },
  ) => void;
  deleteSkill: (skillId: string) => void;
  approveAction: (id: string) => void;
  cancelAction: (id: string) => void;
  openSettings: (section?: SettingsSection) => void;
  closeSettings: () => void;
  setSettingsSection: (section: SettingsSection) => void;
  openProjectSettings: (section?: ProjectSettingsSection) => void;
  closeProjectSettings: () => void;
  setProjectSettingsSection: (section: ProjectSettingsSection) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const ASSISTANT_DELAY_MS = 900;

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  // Phase 4F — one AbortController per chat with an in-flight real backend
  // run, tracked so it can never be silently garbage-collected mid-turn.
  // Aborted on true AppStateProvider unmount (see the effect below) —
  // never on a mere chat switch, since Phase 4E already established that
  // disconnecting the HTTP client doesn't cancel the backend's logical
  // turn anyway, so tearing the connection down on chat switch would only
  // leave that chat's message stuck at "pending" forever with no code
  // path left to resume it.
  //
  // Pre-4H refinement: `stopActiveRun` below is the one OTHER place this
  // is aborted — a genuine user-facing Stop control. Same caveat applies
  // there even more explicitly: aborting this controller only tears down
  // THIS client's HTTP connection to the SSE endpoint; it does not, and
  // cannot, cancel whatever the backend's own turn/ADK Runner is doing
  // server-side (see chat_service.py's own module docstring — verified
  // against the installed Starlette source that a dropped connection is
  // never proactively observed by the streaming response at all). Stop
  // is therefore always an honest "stop listening and show this as
  // stopped locally," never a claim that backend execution itself halted.
  const runControllersRef = useRef(new Map<string, AbortController>());

  useEffect(() => {
    return () => {
      for (const controller of runControllersRef.current.values()) controller.abort();
    };
  }, []);

  // POST-5.1 B3 — one Promise per chat with a session-creation call
  // currently in flight, keyed by chatId. This is the single-flight guard
  // `beginBackendRun` never needed (Phase 4F's own send flow only ever
  // starts one run per chat at a time, gated by `isRunActive` in
  // PromptComposer) but real image attachments can be queued several at
  // once (multi-select, or a paste immediately followed by a picker
  // selection) before any of them has a session to upload against —
  // without this, each queued image would independently see no
  // `backendSessionId` yet and call `createSession()` itself, creating
  // several backend sessions for what the user experiences as one chat.
  // `ensureBackendSession` below is the only reader/writer.
  const sessionCreationPromisesRef = useRef(new Map<string, Promise<string>>());

  // POST-5.1 B3 — one AbortController per in-flight attachment upload,
  // keyed by the DraftImageAttachment's own `id` (mirrors
  // `runControllersRef`'s exact pattern). Removing an uploading
  // attachment aborts its own request; nothing else is affected.
  const uploadControllersRef = useRef(new Map<string, AbortController>());

  useEffect(() => {
    return () => {
      for (const controller of uploadControllersRef.current.values()) controller.abort();
    };
  }, []);

  // POST-5.1 B3 — object-URL lifecycle for draft image previews
  // (instruction section 18: every `URL.createObjectURL` must eventually
  // be revoked). Centralized here, rather than at every individual call
  // site that can cause a DraftImageAttachment to leave the draft (remove,
  // NEW_CHAT, SELECT_CHAT, DELETE_CHAT, CREATE_PROJECT, ENTER_PROJECT,
  // START_TASK_RUN, START_SCHEDULING_CHAT, SEND_MESSAGE — see AppState
  // .reducer.test.ts's own enumeration of every case that resets
  // `draft.attachments`), because that's a lot of call sites to keep in
  // sync by hand and missing even one would leak a URL. Diffing the
  // previous vs. current set of `(id -> objectUrl)` pairs on every
  // `state.draft.attachments` change catches every single one of them
  // uniformly, without touching any of those reducer cases individually.
  // A retried attachment keeps the SAME id/objectUrl (see
  // `retryImageAttachment`), so it never appears "removed then re-added"
  // here and is correctly never revoked mid-retry.
  const draftImageObjectUrlsRef = useRef(new Map<string, string>());

  useEffect(() => {
    const current = new Map<string, string>();
    for (const attachment of state.draft.attachments) {
      if (attachment.kind === "image") current.set(attachment.id, attachment.objectUrl);
    }
    const previous = draftImageObjectUrlsRef.current;
    for (const [id, url] of previous) {
      if (!current.has(id)) URL.revokeObjectURL(url);
    }
    draftImageObjectUrlsRef.current = current;
  }, [state.draft.attachments]);

  // True provider teardown (see the identical pattern for
  // `runControllersRef` above) — revokes whatever object URLs are still
  // outstanding at that point. In normal operation the effect above
  // already revokes everything as attachments leave the draft; this is
  // the final safety net, not the primary mechanism.
  useEffect(() => {
    return () => {
      for (const url of draftImageObjectUrlsRef.current.values()) URL.revokeObjectURL(url);
    };
  }, []);

  // POST-5.1 B3 overflow-UX closure pass — auto-dismisses
  // `draft.attachmentLimitNotice` after a fixed duration. Keyed off
  // `.key` (never the message text) so a second overflow with the exact
  // same copy still restarts the timer instead of inheriting whatever
  // time was left on the first one. Any other action that replaces the
  // whole draft (send, new chat, ...) already clears the notice via
  // `freshDraft()`, so this effect only ever needs to handle the
  // auto-dismiss case, not those resets.
  useEffect(() => {
    if (!state.draft.attachmentLimitNotice) return;
    const timeout = window.setTimeout(() => {
      dispatch({ type: "CLEAR_ATTACHMENT_LIMIT_NOTICE" });
    }, ATTACHMENT_LIMIT_NOTICE_DURATION_MS);
    return () => window.clearTimeout(timeout);
  }, [state.draft.attachmentLimitNotice?.key]);

  // POST-5.1 B4C correction pass — the ONE function that actually performs
  // a `GET /api/sessions` attempt, shared by both the boot effect and an
  // explicit user retry (never two separate implementations). Success
  // merges the summaries in AND settles `savedChatsHydrationStatus` to
  // "loaded" (see the "HYDRATE_SAVED_SESSIONS" reducer case — this
  // includes the "loaded with zero sessions" case, a genuine empty list,
  // never confused with "still loading"/"failed"). Failure dispatches a
  // real, explicit `SAVED_CHATS_HYDRATION_FAILED` (previously just a
  // dev-only console.debug — the earlier version of this effect left the
  // sidebar's Chats section indistinguishable from a genuinely empty
  // account on a real network failure) — it never touches `chats`, so
  // existing local/already-hydrated chats are never erased by a failed
  // retry.
  const runSavedChatsHydration = useCallback(async () => {
    try {
      const response = await listSavedSessions();
      dispatch({ type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: response.sessions } });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Couldn't load saved chats.";
      dispatch({ type: "SAVED_CHATS_HYDRATION_FAILED", payload: { message } });
    }
  }, []);

  // Boot fires this exactly once per app load. A plain boolean ref
  // (rather than relying solely on the reducer's own backendSessionId
  // dedupe — see "HYDRATE_SAVED_SESSIONS") is a single-flight guard
  // against React 18 StrictMode's double-invocation firing a second,
  // wasted `GET /api/sessions` network call; correctness does not depend
  // on it, since the reducer is idempotent even if this ever fired twice.
  // This guard is boot-only — it never blocks `retrySavedChats` below,
  // which is a distinct, always-allowed, explicitly user-triggered call.
  const hasHydratedSavedSessionsRef = useRef(false);
  useEffect(() => {
    if (hasHydratedSavedSessionsRef.current) return;
    hasHydratedSavedSessionsRef.current = true;
    void runSavedChatsHydration();
  }, [runSavedChatsHydration]);

  // POST-5.1 B4C correction pass — explicit user retry for a failed
  // saved-chat-list load (the sidebar's "Retry" affordance). Resets
  // status to "loading" synchronously so the UI reflects the new attempt
  // immediately, then reuses the exact same fetch/merge path as boot —
  // repeated retries can never duplicate a chat, since the reducer's own
  // backendSessionId dedupe is unconditional regardless of how many times
  // this fires.
  const retrySavedChats = useCallback(() => {
    dispatch({ type: "SAVED_CHATS_HYDRATION_STARTED" });
    void runSavedChatsHydration();
  }, [runSavedChatsHydration]);

  // POST-5.1 B4C — lazy transcript hydration: fetches a hydrated saved
  // chat's real history the moment it becomes active, never at boot (see
  // the effect above, which only ever fetches summaries). Re-runs on every
  // `state.chats` change, but the guard below makes every one of those
  // re-runs a cheap no-op except the one genuine "just selected an
  // unloaded saved chat" transition — `historyHydrationStatus` flips away
  // from `"unloaded"` synchronously (HISTORY_FETCH_STARTED, dispatched
  // before the `await`), so a second overlapping fetch for the same chat
  // can never start. The token map additionally guards against a stale
  // response from an OLDER fetch for this same chatId (a manual retry
  // superseding an earlier attempt) ever being applied after a newer one.
  const historyFetchTokensRef = useRef(new Map<string, number>());
  useEffect(() => {
    const chatId = state.activeChatId;
    if (!chatId) return;
    const chat = state.chats[chatId];
    if (!chat || chat.historyHydrationStatus !== "unloaded" || !chat.backendSessionId) return;
    const sessionId = chat.backendSessionId;

    const token = (historyFetchTokensRef.current.get(chatId) ?? 0) + 1;
    historyFetchTokensRef.current.set(chatId, token);
    dispatch({ type: "HISTORY_FETCH_STARTED", payload: { chatId } });

    void (async () => {
      try {
        const response = await getSessionHistory(sessionId);
        // Instruction: a malformed created_at must fail safely for this
        // request rather than ever inventing a fallback time — validated
        // up front, before any state is applied, so HISTORY_FETCH_SUCCEEDED
        // can trust every timestamp unconditionally.
        for (const message of response.messages) {
          if (!Number.isFinite(Date.parse(message.created_at))) {
            throw new Error("malformed history timestamp");
          }
        }
        if (historyFetchTokensRef.current.get(chatId) !== token) return;
        dispatch({ type: "HISTORY_FETCH_SUCCEEDED", payload: { chatId, response } });
      } catch (error) {
        if (historyFetchTokensRef.current.get(chatId) !== token) return;
        const message =
          error instanceof ApiError
            ? error.message
            : "This conversation could not be loaded. Please try again.";
        dispatch({ type: "HISTORY_FETCH_FAILED", payload: { chatId, message } });
      }
    })();
  }, [state.activeChatId, state.chats]);

  const retryHistoryLoad = useCallback((chatId: string) => {
    dispatch({ type: "RETRY_HISTORY_LOAD", payload: { chatId } });
  }, []);

  // Phase 4F — drives one real backend turn. Takes `existingSessionId` as
  // a parameter (rather than reading `state` itself) so it never risks a
  // stale closure over `state.chats`; `sendMessage` below always reads
  // that value fresh from its own (dependency-array-correct) closure
  // before calling this.
  const beginBackendRun = useCallback(
    async (
      chatId: string,
      runToken: string,
      assistantMessageId: string,
      rawText: string,
      existingSessionId: string | undefined,
      attachmentIds: string[] = [],
    ) => {
      const controller = new AbortController();
      runControllersRef.current.set(chatId, controller);
      try {
        let sessionId = existingSessionId;
        if (!sessionId) {
          const created = await createSession();
          sessionId = created.session_id;
          dispatch({ type: "BACKEND_SESSION_CREATED", payload: { chatId, runToken, sessionId } });
        }
        await runBackendChat(
          sessionId,
          rawText,
          {
            onRunStarted: (serverRunId) =>
              dispatch({ type: "BACKEND_RUN_STARTED", payload: { chatId, runToken, serverRunId } }),
            onStatus: (stage, label, activityKind) =>
              dispatch({ type: "BACKEND_STATUS_UPDATE", payload: { chatId, runToken, stage, label, activityKind } }),
            onStatusClear: () => dispatch({ type: "BACKEND_STATUS_CLEAR", payload: { chatId, runToken } }),
            onDelta: (textDelta) =>
              dispatch({
                type: "BACKEND_MESSAGE_DELTA",
                payload: { chatId, runToken, messageId: assistantMessageId, textDelta },
              }),
            onCompleted: (content, source, knowledgeSources) =>
              dispatch({
                type: "BACKEND_MESSAGE_COMPLETED",
                payload: { chatId, runToken, messageId: assistantMessageId, content, source, knowledgeSources },
              }),
            onActionPending: (action: PendingActionDTO) =>
              dispatch({
                type: "BACKEND_ACTION_PENDING",
                payload: { chatId, runToken, messageId: assistantMessageId, action },
              }),
            onSelectionPending: (selection: PendingSelectionDTO) =>
              dispatch({
                type: "BACKEND_SELECTION_PENDING",
                payload: { chatId, runToken, messageId: assistantMessageId, selection },
              }),
            onError: (info) =>
              dispatch({
                type: "BACKEND_RUN_ERROR",
                payload: { chatId, runToken, messageId: assistantMessageId, message: info.message },
              }),
            onRunCompleted: (outcome) =>
              dispatch({ type: "BACKEND_RUN_COMPLETED", payload: { chatId, runToken, outcome } }),
            onTraceStep: (step: TraceStepDTO) =>
              dispatch({
                type: "BACKEND_TRACE_STEP",
                payload: { chatId, runToken, messageId: assistantMessageId, step },
              }),
          },
          controller.signal,
          attachmentIds,
        );
      } catch {
        // createSession() itself failing, before any SSE stream ever opened.
        dispatch({
          type: "BACKEND_RUN_ERROR",
          payload: {
            chatId,
            runToken,
            messageId: assistantMessageId,
            message: "The assistant could not be reached right now. Please try again.",
          },
        });
        dispatch({ type: "BACKEND_RUN_COMPLETED", payload: { chatId, runToken, outcome: "error" } });
      } finally {
        runControllersRef.current.delete(chatId);
      }
    },
    [],
  );

  // POST-5.1 B3 — creates (or reuses) the real backend session a chat
  // needs before its first image attachment can be uploaded. Single-
  // flight per chatId (instruction section 9): a second call for the
  // same chat while the first is still in flight reuses the SAME
  // Promise rather than issuing a second `POST /api/sessions` — see
  // `sessionCreationPromisesRef`'s own docstring above. Never clobbers
  // an already-set `backendSessionId` (`BACKEND_SESSION_ENSURED`'s own
  // reducer guard).
  const ensureBackendSession = useCallback(
    (chatId: string): Promise<string> => {
      const existing = state.chats[chatId]?.backendSessionId;
      if (existing) return Promise.resolve(existing);

      const inFlight = sessionCreationPromisesRef.current.get(chatId);
      if (inFlight) return inFlight;

      const promise = (async () => {
        try {
          const created = await createSession();
          dispatch({ type: "BACKEND_SESSION_ENSURED", payload: { chatId, sessionId: created.session_id } });
          return created.session_id;
        } finally {
          sessionCreationPromisesRef.current.delete(chatId);
        }
      })();
      sessionCreationPromisesRef.current.set(chatId, promise);
      return promise;
    },
    [state.chats],
  );

  // POST-5.1 B3 — the actual upload step for ONE draft image, shared by
  // both `queueImageFiles` (first attempt) and `retryImageAttachment`
  // (instruction section 16: retry reuses the same underlying upload
  // operation, never a parallel implementation). An abort (the
  // attachment was removed mid-upload — see `removeAttachment` below)
  // is silent transport cleanup, never a user-facing failure, mirroring
  // `runBackendChat`'s own `isAbortError` handling.
  const uploadDraftImage = useCallback(
    async (chatId: string, draftId: string, file: File) => {
      dispatch({
        type: "UPDATE_DRAFT_IMAGE_ATTACHMENT",
        payload: { id: draftId, patch: { uploadState: "uploading", error: undefined } },
      });

      const controller = new AbortController();
      uploadControllersRef.current.set(draftId, controller);

      try {
        const sessionId = await ensureBackendSession(chatId);
        const response = await uploadAttachment(sessionId, file, controller.signal);
        // If this draft was removed while the upload was in flight, the
        // reducer's `.map()` simply finds no matching id and this is a
        // silent no-op — a removed attachment is never resurrected by a
        // stale completion (instruction section 37).
        dispatch({
          type: "UPDATE_DRAFT_IMAGE_ATTACHMENT",
          payload: { id: draftId, patch: { uploadState: "ready", attachmentId: response.attachment_id } },
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        dispatch({
          type: "UPDATE_DRAFT_IMAGE_ATTACHMENT",
          payload: { id: draftId, patch: { uploadState: "failed", error: classifyAttachmentUploadFailure(error) } },
        });
      } finally {
        uploadControllersRef.current.delete(draftId);
      }
    },
    [ensureBackendSession],
  );

  /** POST-5.1 B3 — the ONE central image-ingestion path (instruction
   * section 16): the file picker, clipboard paste, and drag/drop all
   * call this exact function, never three separate validation/upload
   * pipelines. Frontend-only preflight (size/count/format — instruction
   * sections 12-13) never replaces the backend's own authoritative
   * validation; it only avoids an always-doomed request.
   *
   * ELIGIBILITY (instruction section 28): mirrors `isBackendBranch`'s own
   * three conditions in `sendMessage` exactly (general workspace scope,
   * no active demo script, no already-drafted ad-hoc "chat room" source)
   * — a chat that wouldn't route its next text send to the real backend
   * anyway is not offered real image attachments; see
   * `ComposerPlusMenu.tsx`/`PromptComposer.tsx` for where this is
   * surfaced to the user as a disabled affordance rather than a silent
   * no-op.
   */
  const queueImageFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;

      const activeChat = state.activeChatId ? state.chats[state.activeChatId] : undefined;
      const eligible =
        state.workspaceScope.type === "general" && state.draft.sources.length === 0 && !activeChat?.demoRun;
      if (!eligible) return;

      const currentImageCount = state.draft.attachments.filter((a) => a.kind === "image").length;
      const remainingCapacity = Math.max(0, MAX_DRAFT_IMAGES - currentImageCount);
      const accepted = files.slice(0, remainingCapacity);

      // POST-5.1 B3 overflow-UX closure pass — a file dropped purely for
      // lack of remaining capacity (never a format/size rejection, which
      // is instead surfaced per-attachment via that draft's own `error`)
      // gets one brief, non-blocking notice rather than silently
      // vanishing. Covers both a partial accept (some queued, some
      // rejected) and a fully-at-capacity call (nothing queued at all).
      if (files.length > accepted.length) {
        dispatch({
          type: "SET_ATTACHMENT_LIMIT_NOTICE",
          payload: { message: `Up to ${MAX_DRAFT_IMAGES} images can be attached.`, key: createId("notice") },
        });
      }
      if (accepted.length === 0) return;

      // POST-5.1 B3: the first image attached to a brand-new composer
      // (no active chat yet) creates the chat immediately — mirrors "the
      // first submitted prompt creates the conversation," extended to
      // "the first attachment does too." `chatId` is minted here, in the
      // callback's own closure, exactly like `sendMessage` already mints
      // `runToken`/message ids before dispatching — never awaited from a
      // re-render.
      const chatId = state.activeChatId ?? createId("chat");
      if (!state.activeChatId) {
        dispatch({ type: "CREATE_DRAFT_CHAT", payload: { chatId, timestamp: Date.now() } });
      }

      const drafts: DraftImageAttachment[] = accepted.map((file) => {
        const mimeType = file.type;
        const isAcceptedType = (ACCEPTED_IMAGE_MIME_TYPES as readonly string[]).includes(
          mimeType as AcceptedImageMimeType,
        );
        const oversized = file.size > MAX_IMAGE_BYTES;
        return {
          kind: "image",
          id: createId("img"),
          file,
          objectUrl: URL.createObjectURL(file),
          uploadState: isAcceptedType && !oversized ? "pending" : "failed",
          filename: file.name,
          mimeType,
          sizeBytes: file.size,
          error: !isAcceptedType
            ? "This image format isn't supported."
            : oversized
              ? "Image is too large."
              : undefined,
        };
      });
      dispatch({ type: "ADD_ATTACHMENTS", payload: { attachments: drafts } });

      for (const draft of drafts) {
        if (draft.uploadState === "pending") void uploadDraftImage(chatId, draft.id, draft.file);
      }
    },
    [state.activeChatId, state.chats, state.workspaceScope.type, state.draft.sources, state.draft.attachments, uploadDraftImage],
  );

  /** POST-5.1 B3 — re-attempts a FAILED draft image's upload, reusing the
   * exact same `File`/preview (instruction section 23): never re-reads
   * from disk, never creates a new object URL.
   */
  const retryImageAttachment = useCallback(
    (draftId: string) => {
      if (!state.activeChatId) return;
      const attachment = state.draft.attachments.find((a) => a.id === draftId);
      if (!attachment || attachment.kind !== "image") return;
      void uploadDraftImage(state.activeChatId, draftId, attachment.file);
    },
    [state.activeChatId, state.draft.attachments, uploadDraftImage],
  );

  /** Pre-4H refinement — the Stop control's implementation. Stops the
   * frontend's own transport/UI IMMEDIATELY and synchronously: aborts
   * this chat's `AbortController` and dispatches `RUN_STOPPED` (see that
   * reducer case — it reuses the same `runToken` ownership guard every
   * other BACKEND_* dispatch already relies on, so any event from this
   * run still in flight at abort time is safely ignored from here on).
   *
   * THEN, best-effort and fire-and-forget from this function's own point
   * of view, asks the backend to really cancel the tracked logical run
   * too (`cancelRun` / `ChatService.cancel_run` — see those docstrings
   * for exactly what server-side cancellation can and cannot guarantee:
   * a synchronous, already-in-flight worker-thread call is not forcibly
   * interruptible). This call is never awaited by the caller and its
   * outcome never changes anything the user sees — the frontend has
   * already stopped locally regardless of whether it succeeds, and
   * nothing here may claim more than is actually known to be true.
   *
   * Only issued once the backend's own `run_id` is actually known
   * (`chat.run.serverRunId`, set once `run.started` arrives) — a Stop
   * clicked before that still stops the frontend immediately; there is
   * simply no `run_id` yet for a server-side cancel call to target.
   *
   * A no-op if `chatId` has no active run. */
  const stopActiveRun = useCallback(
    (chatId: string) => {
      const chat = state.chats[chatId];
      if (!chat?.run) return;
      const { runToken, serverRunId } = chat.run;
      const sessionId = chat.backendSessionId;
      runControllersRef.current.get(chatId)?.abort();
      dispatch({ type: "RUN_STOPPED", payload: { chatId, runToken } });
      if (sessionId && serverRunId) {
        cancelRun(sessionId, serverRunId).catch(() => {
          // Best-effort only — the frontend already stopped locally
          // above regardless of this call's outcome.
        });
      }
    },
    [state.chats],
  );

  // Phase 4G — double-click guard for approve/reject/execute, keyed by
  // "chatId:proposalId". These are short single POST requests (not a
  // long-lived SSE stream like runControllersRef above), so no
  // AbortController/unmount cleanup is needed here — a resolved fetch
  // after unmount just dispatches into a reducer that's still valid.
  const approvalInFlightRef = useRef(new Set<string>());

  const approvePendingAction = useCallback(
    async (chatId: string, proposalId: string) => {
      const chat = state.chats[chatId];
      const sessionId = chat?.backendSessionId;
      // The card that requested this must still be the chat's current
      // backend-active proposal — a click on a superseded historical
      // card (ApprovalCard.tsx's own `canAct` should already have
      // disabled its button, but this is checked again here as the real
      // guard) is a safe no-op, never sent to the backend.
      if (!chat || !sessionId || !proposalId || chat.pendingAction?.proposal_id !== proposalId) return;

      const key = `${chatId}:${proposalId}`;
      if (approvalInFlightRef.current.has(key)) return;
      approvalInFlightRef.current.add(key);

      dispatch({ type: "APPROVAL_APPROVE_STARTED", payload: { chatId, proposalId } });
      try {
        const approveResponse = await approveActionApi(sessionId, proposalId);
        dispatch({
          type: "APPROVAL_APPROVE_SUCCEEDED",
          payload: { chatId, proposalId, pendingAction: approveResponse.pending_action },
        });

        const executeResponse = await executeApprovedAction(sessionId, proposalId);
        const executedAction = executeResponse.executed_action;
        dispatch({
          type: "APPROVAL_EXECUTE_SUCCEEDED",
          payload: {
            chatId,
            proposalId,
            pendingAction: executeResponse.pending_action,
            executedAction: executedAction
              ? { chatId: executedAction.chat_id, title: executedAction.title, webUrl: executedAction.web_url }
              : null,
          },
        });
      } catch (error) {
        const { phase, message } = classifyApprovalFailure(error);
        dispatch({ type: "APPROVAL_REQUEST_FAILED", payload: { chatId, proposalId, phase, message } });
      } finally {
        approvalInFlightRef.current.delete(key);
      }
    },
    [state.chats],
  );

  const rejectPendingAction = useCallback(
    async (chatId: string, proposalId: string) => {
      const chat = state.chats[chatId];
      const sessionId = chat?.backendSessionId;
      if (!chat || !sessionId || !proposalId || chat.pendingAction?.proposal_id !== proposalId) return;

      const key = `${chatId}:${proposalId}`;
      if (approvalInFlightRef.current.has(key)) return;
      approvalInFlightRef.current.add(key);

      dispatch({ type: "APPROVAL_REJECT_STARTED", payload: { chatId, proposalId } });
      try {
        const rejectResponse = await rejectActionApi(sessionId, proposalId);
        dispatch({
          type: "APPROVAL_REJECT_SUCCEEDED",
          payload: { chatId, proposalId, pendingAction: rejectResponse.pending_action },
        });
      } catch (error) {
        const { phase, message } = classifyApprovalFailure(error);
        dispatch({ type: "APPROVAL_REQUEST_FAILED", payload: { chatId, proposalId, phase, message } });
      } finally {
        approvalInFlightRef.current.delete(key);
      }
    },
    [state.chats],
  );

  const toggleActionCardCollapsed = useCallback((chatId: string, messageId: string) => {
    dispatch({ type: "TOGGLE_ACTION_CARD_COLLAPSED", payload: { chatId, messageId } });
  }, []);

  const sendMessage = useCallback((overrideText?: string) => {
    const typedText = (overrideText ?? state.draft.text).trim();

    // POST-5.1 B5 — narrow `DraftAttachment[]` into the two shapes the
    // rest of this function needs: real image drafts (now sendable) and
    // everything else (`Attachment[]`, unchanged — the existing mock/
    // file/folder/pasted-text presentation path, which the `SEND_MESSAGE`
    // payload's `attachments` field has always carried).
    const imageAttachments = state.draft.attachments.filter(
      (a): a is DraftImageAttachment => a.kind === "image",
    );
    const nonImageAttachments = state.draft.attachments.filter(
      (a): a is Attachment => a.kind !== "image",
    );

    // The composer already intercepts long *pastes* and turns them into a
    // "Pasted text.txt" attachment before they ever reach draft.text (see
    // PromptComposer's onPaste handler) — its content is folded back in here
    // so the message's full text always reflects what was actually said,
    // regardless of how much of it lives in an attachment.
    const existingPastedAttachments = nonImageAttachments.filter((a) => a.isPastedText);
    const pastedContent = existingPastedAttachments.map((a) => a.content ?? "").join("\n\n");
    const rawText = [typedText, pastedContent].filter(Boolean).join("\n\n");
    // POST-5.1 B5 — an image-only send (rawText empty, no non-image
    // attachments, but real images present) is now valid; only a
    // genuinely empty draft (no text, no attachments of any kind) is
    // rejected here.
    if (!rawText && nonImageAttachments.length === 0 && imageAttachments.length === 0) return;

    // Anything not already caught at paste time — typed directly, or pasted
    // through a path the composer didn't intercept — still gets converted
    // here as a fallback, so a message is represented the same way no
    // matter how the long text arrived.
    const typedIsLongPaste = typedText.length > LONG_PASTE_THRESHOLD;
    const attachments = typedIsLongPaste
      ? [
          ...nonImageAttachments,
          {
            id: createId("attachment"),
            kind: "file" as const,
            name: "Pasted text.txt",
            meta: `${typedText.length.toLocaleString()} characters`,
            isPastedText: true,
            content: typedText,
          },
        ]
      : nonImageAttachments;

    const chatId = state.activeChatId ?? createId("chat");
    const isNewChat = state.activeChatId === null;
    const userMessageId = createId("msg");
    const assistantMessageId = createId("msg");
    const timestamp = Date.now();

    // A chat enters a deterministic demo script only when its opening prompt
    // matches one of the demo triggers; every later message in that chat just
    // advances to the next scripted assistant turn.
    const existingDemoRun = state.chats[chatId]?.demoRun;
    const newChatDemoRun: DemoRun | undefined = isNewChat
      ? (() => {
          const scenarioId = matchDemoScenario(rawText);
          return scenarioId ? { scenarioId, step: 0 } : undefined;
        })()
      : undefined;
    const activeDemoRun = isNewChat ? newChatDemoRun : existingDemoRun;

    const draftSources = state.draft.sources;

    // Phase 4F: the real backend only ever handles the plain/generic
    // fallback path — never when the user attached an ad-hoc source (that
    // stays the existing mock "read this chat room" feature) or while a
    // chat is following a scripted demo, and never for a Project-scoped
    // chat (the backend has no Project-context equivalent yet; wiring it
    // would silently drop the Project's instructions/connector scoping —
    // a confirmed product decision, not an oversight).
    const isBackendBranch =
      draftSources.length === 0 && !activeDemoRun && state.workspaceScope.type === "general";

    // POST-5.1 B5 — real image attachments can now be sent, but ONLY on
    // the real backend branch, and ONLY once every image is genuinely
    // `ready` with a real backend `attachmentId`. Hard-block here as a
    // defensive backstop to PromptComposer's own `canSend` gate
    // (instruction section 29/30) — never silently drop the image, and
    // never fall through to a mock/demo/project answer that would
    // falsely imply the image was considered.
    if (imageAttachments.length > 0) {
      const allImagesReady = imageAttachments.every((a) => a.uploadState === "ready" && a.attachmentId);
      if (!isBackendBranch || !allImagesReady) return;
    }

    const runToken = isBackendBranch ? createId("run") : undefined;
    const existingBackendSessionId = state.chats[chatId]?.backendSessionId;

    // POST-5.1 B5 — the LOCKED B4D invariant: build the just-sent user
    // message's `persistedAttachments` immediately, from the draft's own
    // already-known successful upload metadata, in draft order — never
    // waiting for a browser refresh/B4D history rehydration to populate
    // it. Metadata only (attachmentId/filename/mimeType/sizeBytes) —
    // never the draft's own File/Blob/objectUrl. `attachmentIds` (the
    // same ids, same order) is what actually goes to the backend.
    const persistedAttachments: PersistedAttachmentReference[] | undefined =
      imageAttachments.length > 0
        ? imageAttachments.map((a) => ({
            attachmentId: a.attachmentId!,
            filename: a.filename,
            mimeType: a.mimeType,
            sizeBytes: a.sizeBytes,
          }))
        : undefined;
    const attachmentIds = imageAttachments.map((a) => a.attachmentId!);

    dispatch({
      type: "SEND_MESSAGE",
      payload: {
        chatId,
        isNewChat,
        userMessageId,
        assistantMessageId,
        text: rawText,
        attachments,
        sources: draftSources,
        timestamp,
        demoRun: newChatDemoRun,
        runToken,
        persistedAttachments,
      },
    });

    if (isBackendBranch) {
      void beginBackendRun(chatId, runToken!, assistantMessageId, rawText, existingBackendSessionId, attachmentIds);
      return;
    }

    // Attaching a chat room is an explicit instruction to go read it, so it
    // takes precedence over the generic keyword heuristics.
    if (draftSources.length > 0) {
      const reads = readSources(draftSources, state.connectors);
      const activeSkillId = state.chats[chatId]?.activeSkillId ?? state.draft.skillId;
      const text = composeSourceBrief(reads, rawText, activeSkillId);
      const skipped = skippedConnectorIds(reads);
      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: {
            messageId: assistantMessageId,
            text,
            suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
          },
        });
      }, ASSISTANT_DELAY_MS);
      return;
    }

    if (activeDemoRun) {
      const script = DEMO_SCRIPTS[activeDemoRun.scenarioId];
      if (activeDemoRun.step < script.length) {
        const turn = script[activeDemoRun.step];
        window.setTimeout(() => {
          dispatch({
            type: "COMPLETE_ASSISTANT_MESSAGE",
            payload: {
              messageId: assistantMessageId,
              text: turn.text,
              citations: turn.citations,
              demoStep: activeDemoRun.step + 1,
            },
          });
        }, ASSISTANT_DELAY_MS);
        return;
      }
    }

    const activeProjectId = state.workspaceScope.type === "project" ? state.workspaceScope.projectId : null;
    const project = activeProjectId ? state.projects[activeProjectId] : null;
    const connector = state.connectors[ACTION_CONNECTOR_ID];
    const connectorAvailable =
      connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

    const response = generateMockAssistantResponse(rawText, {
      connectorAvailable: Boolean(connectorAvailable),
      connectorName: connector?.name ?? "Outlook",
      projectName: project?.name ?? null,
      connectedConnectorIds: connectedConnectorIds(state.connectors),
      forceSchedule: existingDemoRun?.scenarioId === "schedule-setup",
    });

    window.setTimeout(() => {
      dispatchMockResponse(dispatch, assistantMessageId, response, connector);
    }, ASSISTANT_DELAY_MS);
  }, [
    state.draft.text,
    state.draft.attachments,
    state.draft.sources,
    state.draft.skillId,
    state.activeChatId,
    state.workspaceScope,
    state.projects,
    state.connectors,
    state.chats,
    beginBackendRun,
  ]);

  // Interaction-capability extension — double-click guard for
  // choose/skip, keyed by "chatId:selectionId". Same short-single-POST
  // reasoning as `approvalInFlightRef` — no AbortController needed.
  const selectionInFlightRef = useRef(new Set<string>());

  const chooseSelectionOption = useCallback(
    async (chatId: string, selectionId: string, optionId: string) => {
      const chat = state.chats[chatId];
      const sessionId = chat?.backendSessionId;
      // The card that requested this must still be the chat's current
      // selection — a click on a stale/superseded card (SelectionCard's
      // own `canAct` should already have disabled its button, but this
      // is checked again here as the real guard) is a safe no-op.
      if (!chat || !sessionId || chat.pendingSelection?.selection_id !== selectionId) return;
      const ownerMessageId = chat.pendingSelectionMessageId;
      const record = ownerMessageId ? chat.selectionCards?.[ownerMessageId] : undefined;
      if (!record) return;

      const key = `${chatId}:${selectionId}`;
      if (selectionInFlightRef.current.has(key)) return;
      selectionInFlightRef.current.add(key);

      dispatch({ type: "SELECTION_CHOOSE_STARTED", payload: { chatId, selectionId } });
      try {
        const response = await chooseSelectionApi(sessionId, selectionId, optionId);
        dispatch({
          type: "SELECTION_CHOOSE_SUCCEEDED",
          payload: {
            chatId,
            selectionId,
            selectedLabel: response.selected_label,
            pendingAction: response.pending_action,
          },
        });
        // Hardening pass: READ-kind resolution resumes via the backend's
        // own deterministic, destination-free `resume_message` (see
        // selection/read_resume.py) — a real new backend turn, but
        // deliberately NOT `sendMessage` (that would replay the user's
        // original raw text, which still names the OLD, unresolved
        // destination and previously reopened the exact same ambiguity;
        // it would also create a second, synthetic user bubble, which
        // selecting a candidate must never do). `BEGIN_READ_RESUME`
        // seeds only a new assistant placeholder; `beginBackendRun` is
        // the SAME function every normal turn already uses, so
        // streaming/status/elapsed-timer all work identically.
        if (response.resume_message) {
          const resumeAssistantMessageId = createId("msg");
          const resumeRunToken = createId("run");
          dispatch({
            type: "BEGIN_READ_RESUME",
            payload: { chatId, assistantMessageId: resumeAssistantMessageId, runToken: resumeRunToken, timestamp: Date.now() },
          });
          void beginBackendRun(chatId, resumeRunToken, resumeAssistantMessageId, response.resume_message, sessionId);
        }
      } catch (error) {
        const { message } = classifySelectionFailure(error);
        dispatch({ type: "SELECTION_REQUEST_FAILED", payload: { chatId, selectionId, message } });
      } finally {
        selectionInFlightRef.current.delete(key);
      }
    },
    [state.chats, beginBackendRun],
  );

  const skipSelectionOption = useCallback(
    async (chatId: string, selectionId: string) => {
      const chat = state.chats[chatId];
      const sessionId = chat?.backendSessionId;
      if (!chat || !sessionId || chat.pendingSelection?.selection_id !== selectionId) return;

      const key = `${chatId}:${selectionId}`;
      if (selectionInFlightRef.current.has(key)) return;
      selectionInFlightRef.current.add(key);

      dispatch({ type: "SELECTION_SKIP_STARTED", payload: { chatId, selectionId } });
      try {
        await skipSelectionApi(sessionId, selectionId);
        dispatch({ type: "SELECTION_SKIP_SUCCEEDED", payload: { chatId, selectionId } });
      } catch (error) {
        const { message } = classifySelectionFailure(error);
        dispatch({ type: "SELECTION_REQUEST_FAILED", payload: { chatId, selectionId, message } });
      } finally {
        selectionInFlightRef.current.delete(key);
      }
    },
    [state.chats],
  );

  const toggleSelectionCardCollapsed = useCallback((chatId: string, messageId: string) => {
    dispatch({ type: "TOGGLE_SELECTION_CARD_COLLAPSED", payload: { chatId, messageId } });
  }, []);

  const toggleRunTraceExpanded = useCallback((chatId: string, messageId: string) => {
    dispatch({ type: "TOGGLE_RUN_TRACE_EXPANDED", payload: { chatId, messageId } });
  }, []);

  /**
   * Always starts a brand-new chat, regardless of whatever was active before
   * — "New task" should never land the setup conversation inside an unrelated
   * project or thread.
   *
   * With a `seedPrompt` (a template) the intro turn is skipped and the
   * schedule proposal is produced straight away, so one click lands on a
   * filled-in card ready to approve. Without one, the agent introduces itself
   * and asks what to set up.
   */
  const startScheduledTaskSetup = useCallback(
    (options?: { seedPrompt?: string }) => {
      const chatId = createId("chat");
      const userMessageId = createId("msg");
      const assistantMessageId = createId("msg");
      const timestamp = Date.now();
      const seedPrompt = options?.seedPrompt?.trim();

      dispatch({
        type: "START_SCHEDULING_CHAT",
        payload: {
          chatId,
          userMessageId,
          assistantMessageId,
          text: seedPrompt || SCHEDULE_SETUP_OPENING_PROMPT,
          timestamp,
        },
      });

      if (seedPrompt) {
        const response = generateMockAssistantResponse(seedPrompt, {
          connectorAvailable: true,
          connectorName: "Outlook",
          projectName: null,
          connectedConnectorIds: connectedConnectorIds(state.connectors),
          forceSchedule: true,
        });
        window.setTimeout(() => {
          dispatchMockResponse(dispatch, assistantMessageId, response, undefined);
        }, ASSISTANT_DELAY_MS);
        return;
      }

      const introTurn = DEMO_SCRIPTS["schedule-setup"][0];
      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: { messageId: assistantMessageId, text: introTurn.text, demoStep: 1 },
        });
      }, ASSISTANT_DELAY_MS);
    },
    // Reads connector state when seeding a template's schedule proposal.
    [state.connectors],
  );

  const regenerateMessage = useCallback(
    (messageId: string) => {
      const message = state.messages[messageId];
      if (!message || message.role !== "assistant") return;
      const chat = state.chats[message.chatId];
      if (!chat) return;
      // Phase 4F: no regenerate-turn endpoint exists on the real backend
      // yet — silently running the mock generator over a real message
      // would fabricate a misleading "regenerated" answer. The
      // Regenerate control itself is hidden for backend messages
      // (Message.tsx's AssistantMessageActions); this is a defensive
      // guard against any other call site reaching this function.
      if (chat.backendSessionId) return;

      const messageIndex = chat.messageIds.indexOf(messageId);
      const precedingUserMessageId = messageIndex > 0 ? chat.messageIds[messageIndex - 1] : null;
      const promptText = precedingUserMessageId ? (state.messages[precedingUserMessageId]?.text ?? "") : "";

      dispatch({ type: "REGENERATE_MESSAGE", payload: { messageId } });

      // Demo-scripted chats replay that turn's canned text again rather than
      // falling back to the generic mock logic — matched by this message's
      // position among the chat's assistant turns (not the chat's current
      // step, which would only be correct when regenerating the latest one).
      if (chat.demoRun) {
        const script = DEMO_SCRIPTS[chat.demoRun.scenarioId];
        const assistantTurnIds = chat.messageIds.filter((id) => state.messages[id]?.role === "assistant");
        const turn = script[assistantTurnIds.indexOf(messageId)];
        if (turn) {
          window.setTimeout(() => {
            dispatch({
              type: "COMPLETE_ASSISTANT_MESSAGE",
              payload: { messageId, text: turn.text, citations: turn.citations },
            });
          }, ASSISTANT_DELAY_MS);
          return;
        }
      }

      const project = chat.projectId ? state.projects[chat.projectId] : null;
      const connector = state.connectors[ACTION_CONNECTOR_ID];
      const connectorAvailable =
        connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

      const response = generateMockAssistantResponse(promptText, {
        connectorAvailable: Boolean(connectorAvailable),
        connectorName: connector?.name ?? "Outlook",
        projectName: project?.name ?? null,
        connectedConnectorIds: connectedConnectorIds(state.connectors),
        forceSchedule: chat.demoRun?.scenarioId === "schedule-setup",
      });

      window.setTimeout(() => {
        dispatchMockResponse(dispatch, messageId, response, connector);
      }, ASSISTANT_DELAY_MS);
    },
    [state.messages, state.chats, state.projects, state.connectors],
  );

  const editMessage = useCallback(
    (messageId: string, newText: string) => {
      const trimmed = newText.trim();
      if (!trimmed) return;
      const message = state.messages[messageId];
      if (!message || message.role !== "user") return;
      const chat = state.chats[message.chatId];
      if (!chat) return;

      // POST-5.1 B4D correction pass — LOCKED PRODUCT RULE: a user turn
      // that owns a durable, server-linked image attachment is
      // intentionally immutable until multimodal edit/rewrite semantics
      // are explicitly designed (see src/lib/persistedAttachments.ts).
      // This is the REAL enforcement boundary — a structured, hard guard
      // that runs before ANY side effect (no rewindSession call, no
      // createSession call, no text mutation, no EDIT_MESSAGE dispatch,
      // no attachment-state change) — so it protects against a future UI
      // caller, a test invoking this function directly, or any other
      // accidental programmatic call, not just the hidden Edit button in
      // Message.tsx. Text-only user messages are completely unaffected —
      // this check is the ONLY thing added to the existing edit/rewind
      // path below.
      if (hasPersistedImageAttachment(message)) return;

      const assistantMessageId = createId("msg");
      const isFirstMessage = chat.messageIds[0] === messageId;

      // Phase 4G hardening pass: restores editing for backend-sourced
      // chats too (previously hidden entirely). CRITICAL correction over
      // the first restoration attempt: reusing the same session and
      // simply appending the edited text is NOT a true edit — the
      // backend's ADK session history still contains the original
      // message and everything that followed it, so Gemini would see
      // BOTH the old and the new text as stale, hidden context (traced
      // and confirmed against the installed ADK 1.33.0 source: `Runner
      // .run_async` always builds the model's context from the
      // session's full stored event history). The fix: first call the
      // backend's `POST /sessions/{id}/rewind` (a thin wrapper around
      // ADK's own, first-class `Runner.rewind_async` — see
      // chat_service.py's `rewind_before_user_turn`), which marks the
      // edited turn and everything after it as excluded from all future
      // model context on this SAME session (never a new session — there
      // is nothing to switch `backendSessionId` onto). Only once that
      // call has genuinely succeeded do we truncate the visible
      // conversation locally and start the new turn — fail-before-commit,
      // so a rewind failure never leaves the frontend showing a
      // truncated conversation the backend never actually branched.
      if (chat.backendSessionId) {
        const editIndex = chat.messageIds.indexOf(messageId);
        const beforeUserTurnIndex = chat.messageIds
          .slice(0, editIndex)
          .filter((id) => state.messages[id]?.role === "user").length;
        const backendSessionId = chat.backendSessionId;

        void (async () => {
          try {
            await rewindSession(backendSessionId, beforeUserTurnIndex);
          } catch (error) {
            // Nothing was mutated, locally or on the backend — the
            // conversation is left exactly as it was.
            const message =
              error instanceof ApiError
                ? error.message
                : "This message could not be edited right now. Please try again.";
            window.alert(message);
            return;
          }

          const runToken = createId("run");
          dispatch({
            type: "EDIT_MESSAGE",
            payload: {
              chatId: chat.id,
              messageId,
              text: trimmed,
              assistantMessageId,
              retitle: isFirstMessage,
              runToken,
            },
          });
          void beginBackendRun(chat.id, runToken, assistantMessageId, trimmed, backendSessionId);
        })();
        return;
      }

      dispatch({
        type: "EDIT_MESSAGE",
        payload: { chatId: chat.id, messageId, text: trimmed, assistantMessageId, retitle: isFirstMessage },
      });

      // Demo scripts are keyed to the chat's original opening prompt, so an
      // edit (even to the first message) always falls back to the generic
      // mock response rather than trying to resume a script mid-way.
      const project = chat.projectId ? state.projects[chat.projectId] : null;
      const connector = state.connectors[ACTION_CONNECTOR_ID];
      const connectorAvailable =
        connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

      const response = generateMockAssistantResponse(trimmed, {
        connectorAvailable: Boolean(connectorAvailable),
        connectorName: connector?.name ?? "Outlook",
        projectName: project?.name ?? null,
        connectedConnectorIds: connectedConnectorIds(state.connectors),
      });

      window.setTimeout(() => {
        dispatchMockResponse(dispatch, assistantMessageId, response, connector);
      }, ASSISTANT_DELAY_MS);
    },
    [state.messages, state.chats, state.projects, state.connectors, beginBackendRun],
  );

  const newChat = useCallback(() => {
    // Already sitting on a fresh, empty general chat — clicking New chat
    // again shouldn't wipe an in-progress draft for no reason. Nudge the
    // composer instead so it's clear nothing needs to change.
    const alreadyFreshChat =
      state.activeChatId === null &&
      state.mainView === "workspace" &&
      state.workspaceScope.type === "general";
    if (alreadyFreshChat) {
      dispatch({ type: "NUDGE_COMPOSER", payload: { timestamp: Date.now() } });
      return;
    }
    dispatch({ type: "NEW_CHAT" });
  }, [state.activeChatId, state.mainView, state.workspaceScope]);
  const selectChat = useCallback(
    (chatId: string) => dispatch({ type: "SELECT_CHAT", payload: { chatId } }),
    [],
  );
  const setDraftText = useCallback(
    (text: string) => dispatch({ type: "SET_DRAFT_TEXT", payload: { text } }),
    [],
  );
  const setThinkingEffort = useCallback(
    (value: ThinkingEffort) => dispatch({ type: "SET_THINKING_EFFORT", payload: { value } }),
    [],
  );
  const setSkill = useCallback(
    (skillId: string | null) => dispatch({ type: "SET_SKILL", payload: { skillId } }),
    [],
  );
  const setModel = useCallback(
    (modelId: string) => dispatch({ type: "SET_MODEL", payload: { modelId } }),
    [],
  );
  const togglePin = useCallback(
    (chatId: string) =>
      dispatch({ type: "TOGGLE_PIN", payload: { chatId, timestamp: Date.now() } }),
    [],
  );
  const toggleUnread = useCallback(
    (chatId: string) => dispatch({ type: "TOGGLE_UNREAD", payload: { chatId } }),
    [],
  );
  // POST-5.1 B4C — a chat WITH a `backendSessionId` (hydrated from a saved
  // session, or one that already got a real session from its own first
  // send — the two are indistinguishable here and don't need to be) is
  // renamed durably via `PATCH /api/sessions/{id}`; the local `RENAME_CHAT`
  // dispatch only ever happens once that call actually succeeds, using the
  // backend's own echoed title. Everything else (mock/project/demo chats)
  // keeps the original synchronous local-only behavior unchanged.
  // `renameTokensRef` protects against a stale response from an earlier
  // rename overwriting a newer one for the same chat (fire two renames in
  // quick succession, only the LAST one's response may ever apply).
  const renameTokensRef = useRef(new Map<string, number>());
  const renameChat = useCallback(
    (chatId: string, title: string) => {
      const trimmed = title.trim();
      if (!trimmed) return;
      const chat = state.chats[chatId];
      if (!chat) return;

      if (!chat.backendSessionId) {
        dispatch({ type: "RENAME_CHAT", payload: { chatId, title: trimmed } });
        return;
      }

      const backendSessionId = chat.backendSessionId;
      const token = (renameTokensRef.current.get(chatId) ?? 0) + 1;
      renameTokensRef.current.set(chatId, token);

      void (async () => {
        try {
          const response = await renameSessionApi(backendSessionId, trimmed);
          if (renameTokensRef.current.get(chatId) !== token) return;
          dispatch({ type: "RENAME_CHAT", payload: { chatId, title: response.title } });
        } catch (error) {
          if (renameTokensRef.current.get(chatId) !== token) return;
          // Failure leaves the prior title in place (no dispatch at all) —
          // same fail-safe shape as editMessage's own rewind-failure path
          // just above: a safe, already-user-facing message, never raw
          // transport/exception text.
          const message =
            error instanceof ApiError
              ? error.message
              : "This chat could not be renamed right now. Please try again.";
          window.alert(message);
        }
      })();
    },
    [state.chats],
  );
  const deleteChat = useCallback(
    (chatId: string) => dispatch({ type: "DELETE_CHAT", payload: { chatId } }),
    [],
  );
  const toggleConnector = useCallback(
    (connectorId: string) => dispatch({ type: "TOGGLE_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const addAttachments = useCallback(
    (attachments: Attachment[]) => dispatch({ type: "ADD_ATTACHMENTS", payload: { attachments } }),
    [],
  );
  // POST-5.1 B3: aborts an in-flight upload (if any) BEFORE dispatching
  // the removal — the object-URL revocation itself is handled uniformly
  // by the cleanup effect above once `state.draft.attachments` no longer
  // contains this id (instruction section 22).
  //
  // POST-5.1 B7: if the attachment being removed already finished
  // uploading (`uploadState === "ready"`, a real `attachmentId` exists),
  // it is now also deleted server-side — closing the orphan gap B3
  // intentionally left open (a `READY`, unlinked row + GCS object that
  // used to live forever once removed from the draft). This is
  // deliberately BEST-EFFORT and fire-and-forget: the local draft removal
  // above already happened synchronously and must never be blocked,
  // delayed, or rolled back by this call — a failure here (network,
  // already-linked-by-a-race, already-deleted) only means one harmless
  // orphan row/object may remain, never a user-visible error, and never a
  // reason to keep the item in the draft.
  const removeAttachment = useCallback(
    (id: string) => {
      uploadControllersRef.current.get(id)?.abort();
      uploadControllersRef.current.delete(id);

      const draft = state.draft.attachments.find((a) => a.id === id);
      const backendSessionId = state.activeChatId ? state.chats[state.activeChatId]?.backendSessionId : undefined;
      if (
        draft &&
        draft.kind === "image" &&
        draft.uploadState === "ready" &&
        draft.attachmentId &&
        backendSessionId
      ) {
        deleteAttachment(backendSessionId, draft.attachmentId).catch(() => {
          // Best-effort cleanup only -- see this callback's own comment
          // above. Never surfaced to the user; a DEV-only trace is
          // enough to notice a systemic problem during development.
          if (import.meta.env.DEV) {
            console.debug(`[attachments] best-effort delete failed for ${draft.attachmentId}`);
          }
        });
      }

      dispatch({ type: "REMOVE_ATTACHMENT", payload: { id } });
    },
    [state.draft.attachments, state.activeChatId, state.chats],
  );
  const toggleSidebar = useCallback(() => dispatch({ type: "TOGGLE_SIDEBAR" }), []);

  const createProject = useCallback(
    (name: string, description?: string) =>
      dispatch({
        type: "CREATE_PROJECT",
        payload: { projectId: createId("project"), name, description, timestamp: Date.now() },
      }),
    [],
  );
  const enterProject = useCallback(
    (projectId: string) => dispatch({ type: "ENTER_PROJECT", payload: { projectId } }),
    [],
  );
  const setProjectInstructions = useCallback(
    (projectId: string, text: string) =>
      dispatch({ type: "SET_PROJECT_INSTRUCTIONS", payload: { projectId, text } }),
    [],
  );
  const renameProject = useCallback(
    (projectId: string, name: string, description?: string) =>
      dispatch({ type: "RENAME_PROJECT", payload: { projectId, name, description } }),
    [],
  );
  const deleteProject = useCallback(
    (projectId: string) => dispatch({ type: "DELETE_PROJECT", payload: { projectId } }),
    [],
  );
  const toggleProjectPinned = useCallback(
    (projectId: string) =>
      dispatch({ type: "TOGGLE_PROJECT_PINNED", payload: { projectId, timestamp: Date.now() } }),
    [],
  );
  const toggleProjectConnector = useCallback(
    (projectId: string, connectorId: string) =>
      dispatch({ type: "TOGGLE_PROJECT_CONNECTOR", payload: { projectId, connectorId } }),
    [],
  );
  const addProjectFiles = useCallback(
    (projectId: string, files: ProjectFile[]) =>
      dispatch({ type: "ADD_PROJECT_FILES", payload: { projectId, files } }),
    [],
  );
  const removeProjectFile = useCallback(
    (projectId: string, fileId: string) =>
      dispatch({ type: "REMOVE_PROJECT_FILE", payload: { projectId, fileId } }),
    [],
  );
  /** Generates a task's output and deposits it into the chat that task owns.
   * `focus` navigates there; without it the chat just picks up an unread dot. */
  const runScheduledTask = useCallback(
    (taskId: string, options?: { focus?: boolean }) => {
      const task = state.scheduledTasks[taskId];
      if (!task) return;

      const runId = createId("run");
      const messageId = createId("msg");
      const timestamp = Date.now();

      dispatch({
        type: "START_TASK_RUN",
        payload: {
          taskId,
          runId,
          chatId: createId("chat"),
          messageId,
          speakerLabel: formatRunLabel(timestamp),
          timestamp,
          focus: options?.focus ?? false,
        },
      });

      const reads = readSources(task.sources, state.connectors);
      const { text, summary } = composeRunMessage(task, reads, timestamp);
      const skipped = skippedConnectorIds(reads);

      // A run never posts on its own — even under "auto_run" it only offers,
      // and the user approves. Only reachable destinations are offered.
      const reachable = task.distributions
        .map((d) => findDestinationByValue(d.kind, d.target))
        .filter(
          (option): option is NonNullable<typeof option> =>
            !!option && state.connectors[option.connectorId]?.state === "connected",
        );

      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: {
            messageId,
            text,
            suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
            actionProposal:
              reachable.length > 0
                ? {
                    id: createId("action"),
                    connectorId: reachable[0].connectorId,
                    actionType: "post_to_channel",
                    title: `Post to ${reachable.map((o) => o.label).join(" and ")}`,
                    fields: [
                      { label: "Destination", value: reachable.map((o) => o.label).join(", ") },
                      { label: "Content", value: `${task.name} — this run` },
                    ],
                    body: previewForDistribution(text),
                  }
                : undefined,
          },
        });
        dispatch({
          type: "COMPLETE_TASK_RUN",
          payload: { taskId, runId, summary, skippedConnectorIds: skipped },
        });
      }, ASSISTANT_DELAY_MS);
    },
    [state.scheduledTasks, state.connectors],
  );

  /** One backdated, already-complete run, so a freshly created task has
   * something to show. Never seeded for "manual" — a task that has never been
   * triggered must not claim it ran. */
  const seedTaskRun = useCallback(
    (task: ScheduledTask) => {
      const ranAt = previousOccurrence(task, Date.now());
      if (ranAt === null) return;

      const reads = readSources(task.sources, state.connectors);
      const { text, summary } = composeRunMessage(task, reads, ranAt);
      const skipped = skippedConnectorIds(reads);

      dispatch({
        type: "SEED_TASK_RUN",
        payload: {
          taskId: task.id,
          runId: createId("run"),
          chatId: createId("chat"),
          messageId: createId("msg"),
          text,
          summary,
          speakerLabel: formatRunLabel(ranAt),
          ranAt,
          suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
        },
      });
    },
    [state.connectors],
  );


  /** Raises a "post this response to X" proposal. Nothing leaves the app —
   * approval runs the same simulated cascade every other action does. */
  const proposeDistribution = useCallback(
    (messageId: string, destinationId: string) => {
      const message = state.messages[messageId];
      const destination = findDestination(destinationId);
      if (!message || !destination) return;

      dispatch({
        type: "PROPOSE_DISTRIBUTION",
        payload: {
          proposalId: createId("action"),
          messageId,
          connectorId: destination.connectorId,
          destinationLabel: destination.label,
          body: previewForDistribution(message.text),
        },
      });
    },
    [state.messages],
  );

  const addDraftSource = useCallback(
    (source: TaskSource) => dispatch({ type: "ADD_DRAFT_SOURCE", payload: { source } }),
    [],
  );
  const removeDraftSource = useCallback(
    (id: string) => dispatch({ type: "REMOVE_DRAFT_SOURCE", payload: { id } }),
    [],
  );
  const updateScheduledTask = useCallback(
    (task: ScheduledTask) => dispatch({ type: "UPDATE_SCHEDULED_TASK", payload: task }),
    [],
  );
  const deleteScheduledTask = useCallback(
    (id: string) => dispatch({ type: "DELETE_SCHEDULED_TASK", payload: { id } }),
    [],
  );
  const toggleScheduledTaskActive = useCallback(
    (id: string) => dispatch({ type: "TOGGLE_SCHEDULED_TASK_ACTIVE", payload: { id } }),
    [],
  );
  const openAllProjects = useCallback(() => dispatch({ type: "OPEN_ALL_PROJECTS" }), []);
  const closeAllProjects = useCallback(() => dispatch({ type: "CLOSE_ALL_PROJECTS" }), []);
  const openScheduledTasks = useCallback(() => dispatch({ type: "OPEN_SCHEDULED_TASKS" }), []);
  const viewScheduledTask = useCallback(
    (id: string) => dispatch({ type: "VIEW_SCHEDULED_TASK", payload: { id } }),
    [],
  );
  const moveChatToProject = useCallback(
    (chatId: string, projectId: string | null) =>
      dispatch({
        type: "MOVE_CHAT_TO_PROJECT",
        payload: { chatId, projectId, timestamp: Date.now() },
      }),
    [],
  );

  const connectConnector = useCallback(
    (connectorId: string) => dispatch({ type: "CONNECT_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const disconnectConnector = useCallback(
    (connectorId: string) => dispatch({ type: "DISCONNECT_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const createSkill = useCallback(
    (input: { name: string; description: string; instructions: string }) =>
      dispatch({ type: "CREATE_SKILL", payload: { skillId: createId("skill"), ...input } }),
    [],
  );
  const updateSkill = useCallback(
    (skillId: string, input: { name: string; description: string; instructions: string }) =>
      dispatch({ type: "UPDATE_SKILL", payload: { skillId, ...input } }),
    [],
  );
  const deleteSkill = useCallback(
    (skillId: string) => dispatch({ type: "DELETE_SKILL", payload: { skillId } }),
    [],
  );

  const approveAction = useCallback(
    (id: string) => {
      const proposal = state.actionProposals[id];
      dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "approved" } });
      window.setTimeout(() => {
        dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "processing" } });
        window.setTimeout(() => {
          // Scheduling is the one proposal type that actually produces a
          // real (mocked) side effect — every other action type just plays
          // out the generic approve/process/complete timeline.
          let createdTaskId: string | undefined;
          if (proposal?.actionType === "schedule_task" && proposal.scheduledTaskDraft) {
            createdTaskId = createId("task");
            const task: ScheduledTask = {
              id: createdTaskId,
              createdAt: Date.now(),
              name: proposal.scheduledTaskDraft.name,
              instructions: proposal.scheduledTaskDraft.instructions,
              modelId: DEFAULT_MODEL_ID,
              frequency: proposal.scheduledTaskDraft.frequency,
              timeOfDay: proposal.scheduledTaskDraft.timeOfDay,
              sources: proposal.scheduledTaskDraft.sources,
              distributions: [],
              permission: "manual_approve",
              // The conversation that set this up becomes the task's output
              // chat, so approving yields one sidebar row rather than two.
              chatId: proposal.chatId,
              runs: [],
              active: true,
            };
            // CREATE_SCHEDULED_TASK adopts and renames the chat itself — doing
            // it in the reducer keeps it atomic instead of reading this
            // callback's stale `state` snapshot.
            dispatch({ type: "CREATE_SCHEDULED_TASK", payload: task });
            seedTaskRun(task);
          }
          dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "completed", createdTaskId } });
        }, 900);
      }, 350);
    },
    // seedTaskRun is recreated whenever connectors change; without it here,
    // connect-then-approve would seed the run from pre-connect state. Note
    // state.chats is NOT needed — chat adoption moved into the reducer.
    [state.actionProposals, seedTaskRun],
  );
  const cancelAction = useCallback(
    (id: string) => dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "cancelled" } }),
    [],
  );
  const openSettings = useCallback(
    (section?: SettingsSection) => dispatch({ type: "OPEN_SETTINGS", payload: { section } }),
    [],
  );
  const closeSettings = useCallback(() => dispatch({ type: "CLOSE_SETTINGS" }), []);
  const setSettingsSection = useCallback(
    (section: SettingsSection) => dispatch({ type: "SET_SETTINGS_SECTION", payload: { section } }),
    [],
  );
  const openProjectSettings = useCallback(
    (section?: ProjectSettingsSection) =>
      dispatch({ type: "OPEN_PROJECT_SETTINGS", payload: { section } }),
    [],
  );
  const closeProjectSettings = useCallback(() => dispatch({ type: "CLOSE_PROJECT_SETTINGS" }), []);
  const setProjectSettingsSection = useCallback(
    (section: ProjectSettingsSection) =>
      dispatch({ type: "SET_PROJECT_SETTINGS_SECTION", payload: { section } }),
    [],
  );

  const activeChat = state.activeChatId ? state.chats[state.activeChatId] ?? null : null;

  const activeMessages = useMemo(() => {
    if (!activeChat) return [];
    return activeChat.messageIds.map((id) => state.messages[id]).filter(Boolean);
  }, [activeChat, state.messages]);

  const activeSkillId = activeChat ? activeChat.activeSkillId : state.draft.skillId;
  const activeConnectorIds = activeChat ? activeChat.connectorIds : state.draft.connectorIds;
  const activeModelId = activeChat ? activeChat.selectedModelId : state.draft.modelId;
  const activeThinkingEffort = activeChat ? activeChat.thinkingEffort : state.draft.thinkingEffort;

  const chatList = useMemo(
    () => state.chatOrder.map((id) => state.chats[id]).filter(Boolean),
    [state.chatOrder, state.chats],
  );

  const projects = useMemo(
    () => state.projectOrder.map((id) => state.projects[id]).filter(Boolean),
    [state.projectOrder, state.projects],
  );

  const activeProject =
    state.workspaceScope.type === "project" ? state.projects[state.workspaceScope.projectId] ?? null : null;

  const connectorList = useMemo(
    () => MOCK_CONNECTORS.map((c) => state.connectors[c.id] ?? c),
    [state.connectors],
  );

  const skillList = useMemo(
    () => state.skillOrder.map((id) => state.skills[id]).filter(Boolean),
    [state.skillOrder, state.skills],
  );

  const value: AppContextValue = {
    state,
    activeChat,
    activeMessages,
    activeSkillId,
    activeConnectorIds,
    activeModelId,
    activeThinkingEffort,
    chatList,
    projects,
    activeProject,
    connectorList,
    skillList,
    sendMessage,
    startScheduledTaskSetup,
    approvePendingAction,
    rejectPendingAction,
    toggleActionCardCollapsed,
    chooseSelectionOption,
    skipSelectionOption,
    toggleSelectionCardCollapsed,
    toggleRunTraceExpanded,
    stopActiveRun,
    regenerateMessage,
    editMessage,
    newChat,
    selectChat,
    setDraftText,
    setThinkingEffort,
    setSkill,
    setModel,
    togglePin,
    toggleUnread,
    renameChat,
    deleteChat,
    retryHistoryLoad,
    retrySavedChats,
    toggleConnector,
    addAttachments,
    removeAttachment,
    queueImageFiles,
    retryImageAttachment,
    toggleSidebar,
    createProject,
    enterProject,
    setProjectInstructions,
    renameProject,
    deleteProject,
    toggleProjectPinned,
    toggleProjectConnector,
    addProjectFiles,
    removeProjectFile,
    updateScheduledTask,
    runScheduledTask,
    addDraftSource,
    removeDraftSource,
    proposeDistribution,
    deleteScheduledTask,
    toggleScheduledTaskActive,
    openAllProjects,
    closeAllProjects,
    openScheduledTasks,
    viewScheduledTask,
    moveChatToProject,
    connectConnector,
    disconnectConnector,
    createSkill,
    updateSkill,
    deleteSkill,
    approveAction,
    cancelAction,
    openSettings,
    closeSettings,
    setSettingsSection,
    openProjectSettings,
    closeProjectSettings,
    setProjectSettingsSection,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppState() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
