import { Check, ChevronDown, CircleX, Loader2, TriangleAlert, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
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
        <span className="flex-1 text-sm font-medium text-primary">{headline(view.kind, createChat)}</span>
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
                <DetailRow
                  label="Destination"
                  value={pendingAction.target_display_name || "Selected Teams conversation"}
                />
                <DetailRow label="Message" value={pendingAction.message || "—"} />
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
