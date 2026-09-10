import { Check, ChevronDown, CircleX, Loader2, TriangleAlert, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import type { PendingActionDTO } from "../../api/types";
import { deriveApprovalCardView, type ApprovalCardView } from "../../lib/approvalCard";
import { getActionOperationPresentation } from "../../lib/actionOperationLabels";
import { cn } from "../../lib/cn";

const GENERIC_EXPIRED_MESSAGE = "This approval has expired. Please prepare the action again.";
const GENERIC_FAILED_MESSAGE = "Something went wrong. Please try again.";
const GENERIC_UNCONFIRMED_MESSAGE =
  "We couldn't confirm whether this went through. Please check Teams directly before trying again.";

/** True for teams.createChat; false for teams.sendMessage — the only two
 * operations the frozen backend contract supports. */
function isCreateChat(operation: string): boolean {
  return operation !== "teams.sendMessage";
}

/** One canonical "where did/will this happen" summary — the chat title
 * being created, or the sendMessage destination's authoritative display
 * name — with the exact same fallback text the expanded Destination/Chat
 * title rows have always used. Shared by that expanded row and (UX-1) the
 * compact historical collapsed-state summary line, so the two can never
 * drift apart. */
function destinationSummary(pendingAction: PendingActionDTO, createChat: boolean): string {
  return createChat
    ? pendingAction.title || "—"
    : pendingAction.target_display_name || "Selected Teams conversation";
}

/**
 * The action card for one specific proposal, permanently anchored to the
 * assistant message/turn that emitted it (Phase 4G hardening pass — see
 * `Chat.actionCards`). Never moves to sit under newer messages, and never
 * re-renders as a second/duplicate card elsewhere: exactly one card per
 * proposal, exactly one message owns it.
 *
 * Collapses to a compact one-line status row whenever a newer user
 * message is sent (see the `SEND_MESSAGE` reducer case) and expands back
 * to its full content on click — never on a timer. A still-pending
 * historical card remains fully actionable once expanded, as long as its
 * proposal is still the chat's current backend-active one (`canAct`
 * below) — resolved conversation turns happening elsewhere never
 * invalidate it client-side; the backend's own expiry/staleness rules
 * remain the only authority for that.
 *
 * Review + decide only — no field here is ever editable. Never renders
 * `expires_at`/`expires_in_seconds`/`expires_in_minutes`/`payload_hash`/
 * the raw `status` string, or a raw Teams `chat_id` as the destination
 * for a sendMessage action — the Destination row uses
 * `pendingAction.target_display_name` (an authoritative, backend-sourced
 * presentation field — never inferred/parsed on the frontend), falling
 * back to a neutral placeholder only when it is absent.
 *
 * UX-1 — historical completed-action presentation: a completed card whose
 * owning message is no longer the chat's latest message renders, WHILE
 * COLLAPSED ONLY, "Earlier action completed" plus a compact destination
 * line instead of the ambiguous plain "Action completed" — so a user
 * cannot mistake an older, already-executed write for something the
 * current (possibly read-only) turn just did. Expanding it reverts to the
 * ordinary "Action completed" headline plus full detail, exactly as
 * before — the historical distinction only matters for the compact,
 * one-line summary. Presentation only: ownership, collapse/expand state,
 * approval, and execution semantics are entirely unchanged.
 */
export function ApprovalCard({ chatId, messageId }: { chatId: string; messageId: string }) {
  const { state, approvePendingAction, rejectPendingAction, toggleActionCardCollapsed } = useAppState();
  const chat = state.chats[chatId];
  const record = chat?.actionCards?.[messageId];
  if (!record) return null;

  const pendingAction = record.pendingAction;
  const view = deriveApprovalCardView(pendingAction, record.approvalCard);
  const createChat = isCreateChat(pendingAction.operation);
  const { primaryButtonLabel } = getActionOperationPresentation(pendingAction.operation);

  const busy = view.kind === "approving" || view.kind === "executing" || view.kind === "rejecting";
  // Only the chat's currently backend-active proposal is actionable — an
  // older, superseded record (a newer proposal now owns chat.pendingAction)
  // is permanently read-only history, never a stale button that could act
  // on the wrong proposal.
  const isCurrentProposal = chat?.pendingAction?.proposal_id === record.proposalId;
  const canAct = view.kind === "pending" && isCurrentProposal;
  const showActions = canAct || (busy && isCurrentProposal);

  const expanded = !record.collapsed;

  // UX-1: a completed action whose owning message is no longer the chat's
  // latest message (i.e. at least one later turn already exists) is
  // TRUTHFULLY historical relative to the current conversation position —
  // determined ONLY from stable message-id/array-position state (never a
  // timer, timestamp, DOM query, or prompt/destination text), mirroring
  // exactly the same `chat.messageIds` ordering `EDIT_MESSAGE`'s own
  // ownership cleanup already relies on. Deliberately independent of
  // `record.collapsed` (a separate, user-toggleable presentation flag) —
  // a historical card the user manually re-expands must NOT keep showing
  // "Earlier" wording once its full detail (destination/message/"Message
  // sent") already makes its historical nature explicit; only its
  // collapsed one-line summary needs the disambiguating wording. This is
  // presentation-only: ownership (`chat.actionCards`), `pendingAction`/
  // `pendingActionMessageId`, and approval/execution semantics are
  // completely untouched.
  const chatMessageIds = chat?.messageIds ?? [];
  const isFromEarlierTurn =
    chatMessageIds.length > 0 && chatMessageIds[chatMessageIds.length - 1] !== messageId;
  const showEarlierCompletedWording = view.kind === "completed" && isFromEarlierTurn && !expanded;
  const headlineText = showEarlierCompletedWording ? "Earlier action completed" : headline(view.kind, createChat);

  return (
    <div
      role="group"
      aria-label="Pending Teams action approval"
      aria-busy={busy}
      className={cn(
        "anim-fade mt-3 max-w-[420px] rounded-xl border px-4 py-3.5 transition-colors duration-200",
        toneClasses(view.kind),
      )}
    >
      <button
        type="button"
        onClick={() => toggleActionCardCollapsed(chatId, messageId)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 rounded-md"
      >
        <StatusIcon kind={view.kind} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
          <span className="text-sm font-medium text-primary">{headlineText}</span>
          {showEarlierCompletedWording && (
            <span className="truncate text-xs text-tertiary">{destinationSummary(pendingAction, createChat)}</span>
          )}
        </span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-tertiary action-card-chevron", expanded && "is-expanded")}
          aria-hidden="true"
        />
      </button>

      {expanded && (
        <div className="anim-fade">
          <div className="mt-2.5 flex flex-col gap-1 text-sm approval-card-mono">
            {createChat ? (
              <>
                <DetailRow label="Chat title" value={pendingAction.title || "—"} />
                <DetailRow label="Participants" value={pendingAction.members.join(", ") || "—"} />
              </>
            ) : (
              <>
                <DetailRow label="Destination" value={destinationSummary(pendingAction, false)} />
                <MessageDetailRow message={pendingAction.message} />
              </>
            )}
          </div>

          {view.kind === "expired" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-warning">
              {view.message || GENERIC_EXPIRED_MESSAGE}
            </p>
          )}

          {view.kind === "unconfirmed" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-tertiary">
              {view.message || GENERIC_UNCONFIRMED_MESSAGE}
            </p>
          )}

          {view.kind === "failed" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-danger">
              {view.message || GENERIC_FAILED_MESSAGE}
            </p>
          )}

          {view.kind === "completed" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-success">
              {createChat ? "Chat created." : "Message sent."}
              {view.executedAction?.webUrl && (
                <>
                  {" "}
                  <a
                    href={view.executedAction.webUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-accent hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                  >
                    Open in Teams
                  </a>
                </>
              )}
            </p>
          )}

          {view.kind === "rejected" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-tertiary">
              Action rejected.
            </p>
          )}

          {showActions && (
            <div className="mt-3.5 flex items-center justify-end gap-2" aria-live="polite">
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-tertiary" aria-hidden="true" />
              ) : (
                <>
                  <button
                    type="button"
                    disabled={!canAct}
                    onClick={() => rejectPendingAction(chatId, record.proposalId)}
                    className="inline-flex h-7 items-center rounded-lg px-2.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    disabled={!canAct}
                    onClick={() => approvePendingAction(chatId, record.proposalId)}
                    className="inline-flex h-7 items-center rounded-lg bg-accent px-2.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-50"
                  >
                    {primaryButtonLabel}
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function toneClasses(kind: ApprovalCardView["kind"]): string {
  switch (kind) {
    case "pending":
      return "border-warning/60 bg-surface-raised";
    case "completed":
      return "border-success/60 bg-surface-raised";
    case "rejected":
      return "border-subtle/50 bg-transparent";
    case "expired":
      return "border-warning/40 bg-transparent";
    case "unconfirmed":
      return "border-subtle/60 bg-surface-raised";
    case "failed":
      return "border-danger/60 bg-surface-raised";
    default:
      // approving / executing / rejecting — neutral, no color signal for
      // transient in-flight work.
      return "border-subtle/60 bg-surface-raised";
  }
}

function StatusIcon({ kind }: { kind: ApprovalCardView["kind"] }) {
  switch (kind) {
    case "completed":
      return <Check className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />;
    case "rejected":
      return <X className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />;
    case "expired":
      return <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />;
    case "unconfirmed":
      return <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />;
    case "failed":
      return <CircleX className="h-3.5 w-3.5 shrink-0 text-danger" aria-hidden="true" />;
    case "approving":
    case "executing":
    case "rejecting":
      return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-tertiary" aria-hidden="true" />;
    default:
      return <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />; // pending
  }
}

function headline(kind: ApprovalCardView["kind"], createChat: boolean): string {
  switch (kind) {
    case "pending":
      return "Action requires approval";
    case "approving":
      return "Approving…";
    case "executing":
      return createChat ? "Creating the chat…" : "Sending the message…";
    case "completed":
      return "Action completed";
    case "rejecting":
      return "Rejecting…";
    case "rejected":
      return "Action rejected";
    case "expired":
      return "Approval expired";
    case "unconfirmed":
      return "Execution status could not be confirmed";
    case "failed":
      return "Action failed";
  }
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1.5">
      <span className="shrink-0 text-tertiary">{label}:</span>
      <span className="break-words text-secondary">{value}</span>
    </div>
  );
}

/** POST-B7 UI/UX refinement (Item 1) — `pendingAction.message` for a
 * `teams.sendMessage` proposal is now a small, backend-formatted HTML
 * fragment (see `backend/tools/teams/message_formatting.py`), never raw
 * markdown/plain text dumped as-is. Rendered through
 * `renderSafeTeamsMessageHtml` (an allowlist-only parser, never
 * `dangerouslySetInnerHTML`) so the user reviews the SAME structure
 * (paragraphs/lists/short headings) that will actually reach Teams,
 * instead of literal `<p>`/`<ul>` tags as visible text. */
/** POST-B7 UI/UX refinement (Item 1), CORRECTIVE PASS — `pendingAction
 * .message` for a `teams.sendMessage` proposal is deterministic
 * STRUCTURED PLAIN TEXT (see `backend/tools/teams/message_formatting
 * .py`), never HTML — a real live test proved the Power Automate/Teams
 * write path does not render HTML as intended. Rendered as plain React
 * text (`{message}`, auto-escaped, no `dangerouslySetInnerHTML`, no HTML
 * parsing of any kind needed) with `whitespace-pre-wrap` so the
 * formatter's own blank-line paragraph separation and per-line list
 * markers (already real `\n` characters in the string) are visually
 * preserved exactly as approved — never collapsed by default HTML
 * whitespace rules. */
function MessageDetailRow({ message }: { message: string | null }) {
  return (
    <div className="flex gap-1.5">
      <span className="shrink-0 text-tertiary">Message:</span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-secondary">{message || "—"}</span>
    </div>
  );
}
