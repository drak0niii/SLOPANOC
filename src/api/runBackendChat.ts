import { ApiError } from "./client";
import { isAbortError, streamChatMessage } from "./streamChat";
import type {
  KnowledgeSourceReferenceDTO,
  PendingActionDTO,
  PendingSelectionDTO,
  SourceReferenceDTO,
  TraceStepDTO,
} from "./types";

const GENERIC_CONNECTION_ERROR =
  "The assistant could not be reached right now. Please try again.";

/**
 * Typed callbacks for each of the 8 frozen SLOPANOC stream event types.
 * Deliberately plain functions, not `dispatch`/`Action` — this file (and
 * the rest of `src/api/`) owns zero knowledge of the app's reducer, so
 * `src/state/AppState.tsx` is the only place that wires these to
 * `dispatch({...})` calls. Keeps the API layer independently testable and
 * free of a circular dependency on application state.
 */
export interface BackendChatHandlers {
  onRunStarted: (serverRunId: string) => void;
  /** `activityKind` (Phase 2, Runtime Activity Truthfulness) — present
   * only for a status backed by a real, observed `ActivityEvent`; `null`
   * for every pre-existing, non-activity-driven status. */
  onStatus: (stage: string, label: string, activityKind: string | null) => void;
  onStatusClear: () => void;
  onDelta: (textDelta: string) => void;
  /** Pre-4H UX/provenance milestone — `source`, when present, is the
   * safe, structured Teams provenance for THIS answer (never parsed from
   * `content`; see SourceChip.tsx's module docstring). Phase 5.1J
   * correction pass (Part C) — `knowledgeSources`, when present and
   * non-empty, is the governed-knowledge counterpart; independent of
   * `source` (either, both, or neither may be present). */
  onCompleted: (content: string, source?: SourceReferenceDTO, knowledgeSources?: KnowledgeSourceReferenceDTO[]) => void;
  onActionPending: (action: PendingActionDTO) => void;
  /** Interaction-capability extension — mirrors `onActionPending` exactly. */
  onSelectionPending: (selection: PendingSelectionDTO) => void;
  onError: (info: { code?: string; message: string }) => void;
  onRunCompleted: (outcome: "ok" | "error") => void;
  /** Expandable, sanitized run trace (pre-4H milestone) — one call per
   * deterministic, already-safe runtime milestone. Never chain-of-thought;
   * see RunTrace.tsx's module docstring. */
  onTraceStep: (step: TraceStepDTO) => void;
}

function toSafeErrorInfo(error: unknown): { message: string } {
  if (error instanceof ApiError) return { message: error.message };
  if (import.meta.env.DEV) console.debug("[chat] transport error", error);
  return { message: GENERIC_CONNECTION_ERROR };
}

/**
 * Drives one chat turn against the real backend and dispatches it to
 * `handlers` in the order events are received. Not a React hook — its
 * only caller is a plain async helper inside `AppStateProvider`
 * (`beginBackendRun`), itself invoked from the `sendMessage` callback;
 * there's no component lifecycle here that would benefit from hook
 * machinery, and keeping this a plain function makes it trivially
 * testable with spy handlers and no React involved at all.
 *
 * Guarantees the run always terminates from the caller's point of view:
 * a stream that ends without ever emitting `run.completed` (a defensive
 * case, not expected from the frozen backend contract) still resolves
 * into `onError` + `onRunCompleted("error")`, so the UI can never be left
 * stuck showing an active run. An aborted request (component
 * unmount/teardown — see AppState.tsx) is treated as silent transport
 * cleanup, never a user-facing error.
 */
export async function runBackendChat(
  sessionId: string,
  message: string,
  handlers: BackendChatHandlers,
  signal: AbortSignal,
  attachmentIds: string[] = [],
): Promise<void> {
  let sawRunCompleted = false;

  try {
    await streamChatMessage({
      sessionId,
      message,
      attachmentIds,
      signal,
      onEvent: (event) => {
        switch (event.type) {
          case "run.started":
            handlers.onRunStarted(event.run_id);
            break;
          case "status":
            handlers.onStatus(event.data.stage, event.data.label, event.data.activity_kind ?? null);
            break;
          case "status.clear":
            handlers.onStatusClear();
            break;
          case "message.delta":
            handlers.onDelta(event.data.text);
            break;
          case "message.completed":
            handlers.onCompleted(event.data.content, event.data.source, event.data.knowledge_sources);
            break;
          case "action.pending":
            handlers.onActionPending(event.data);
            break;
          case "selection.pending":
            handlers.onSelectionPending(event.data);
            break;
          case "error":
            handlers.onError(event.data);
            break;
          case "run.completed":
            sawRunCompleted = true;
            handlers.onRunCompleted(event.data.outcome);
            break;
          case "trace.step":
            handlers.onTraceStep(event.data);
            break;
        }
      },
    });

    if (!sawRunCompleted) {
      handlers.onError({ message: GENERIC_CONNECTION_ERROR });
      handlers.onRunCompleted("error");
    }
  } catch (error) {
    if (isAbortError(error)) return; // transport teardown only, never user-facing
    handlers.onError(toSafeErrorInfo(error));
    handlers.onRunCompleted("error");
  }
}
