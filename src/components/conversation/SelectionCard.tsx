import { Check, ChevronDown, ChevronRight, CircleX, Loader2, TriangleAlert, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { deriveSelectionCardView, type SelectionCardView } from "../../lib/selectionCard";
import { getSelectionCollapsedLabel } from "../../lib/selectionPresentation";
import { cn } from "../../lib/cn";

const GENERIC_FAILED_MESSAGE = "Something went wrong. Please try again.";

/**
 * The interactive card for one specific Teams chat-name disambiguation,
 * permanently anchored to the assistant message/turn that emitted it —
 * mirrors ApprovalCard.tsx's ownership/collapse model exactly (see
 * `Chat.selectionCards`), but is a structurally distinct, generic
 * "choose one of up to 3 options, or skip" card — never a hardcoded
 * "Teams typo card."
 *
 * Choosing an option is explicitly NOT approval: for a pending write,
 * choosing only produces a normal ActionProposal (a separate
 * `ApprovalCard` then appears on its own, per the required sequence —
 * see selection_service.py's module docstring) — this card never sends
 * anything to Teams itself.
 *
 * Never renders a raw Teams chat_id — every option's `label` is already
 * the only safe, human-readable value the backend ever sends for it (see
 * `PendingSelectionDTO`/`SelectionOptionDTO`).
 */
export function SelectionCard({ chatId, messageId }: { chatId: string; messageId: string }) {
  const { state, chooseSelectionOption, skipSelectionOption, toggleSelectionCardCollapsed } = useAppState();
  const chat = state.chats[chatId];
  const record = chat?.selectionCards?.[messageId];
  if (!record) return null;

  const selection = record.pendingSelection;
  const isCurrent = chat?.pendingSelection?.selection_id === record.selectionId;
  const view = deriveSelectionCardView(isCurrent, record.selectionCard);

  const busy = view.kind === "choosing" || view.kind === "skipping";
  const canAct = view.kind === "pending";
  const showActions = canAct || busy;

  const expanded = !record.collapsed;

  return (
    <div
      role="group"
      aria-label="Teams chat selection"
      aria-busy={busy}
      className={cn(
        "anim-fade mt-3 max-w-[420px] rounded-xl border px-4 py-3.5 transition-colors duration-200",
        toneClasses(view.kind),
      )}
    >
      <button
        type="button"
        onClick={() => toggleSelectionCardCollapsed(chatId, messageId)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 rounded-md"
      >
        <StatusIcon kind={view.kind} />
        <span className="flex-1 text-sm font-medium text-primary">
          {expanded ? "Which chat did you mean?" : getSelectionCollapsedLabel(view)}
        </span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 text-tertiary action-card-chevron", expanded && "is-expanded")}
          aria-hidden="true"
        />
      </button>

      {expanded && (
        <div className="anim-fade">
          {showActions && (
            <div className="mt-2.5 flex flex-col gap-1">
              {selection.options.map((option) => (
                <button
                  key={option.option_id}
                  type="button"
                  disabled={!canAct}
                  onClick={() => chooseSelectionOption(chatId, record.selectionId, option.option_id)}
                  className="group flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm text-primary transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-50"
                >
                  <span className="break-words">{option.label}</span>
                  <ChevronRight
                    className="h-3.5 w-3.5 shrink-0 text-tertiary opacity-0 transition-opacity duration-150 group-hover:opacity-100"
                    aria-hidden="true"
                  />
                </button>
              ))}
            </div>
          )}

          {view.kind === "resolved" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-success">
              Teams chat selected: {view.selectedLabel ?? "—"}
            </p>
          )}

          {view.kind === "skipped" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-tertiary">
              No Teams chat selected.
            </p>
          )}

          {view.kind === "stale" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-tertiary">
              This selection is no longer active — the conversation has moved on.
            </p>
          )}

          {view.kind === "failed" && (
            <p role="status" className="approval-card-mono mt-2.5 text-sm text-danger">
              {view.message || GENERIC_FAILED_MESSAGE}
            </p>
          )}

          {showActions && (
            <div className="mt-3.5 flex items-center justify-end gap-2" aria-live="polite">
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-tertiary" aria-hidden="true" />
              ) : (
                <button
                  type="button"
                  disabled={!canAct}
                  onClick={() => skipSelectionOption(chatId, record.selectionId)}
                  className="inline-flex h-7 items-center rounded-lg px-2.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-50"
                >
                  Skip
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function toneClasses(kind: SelectionCardView["kind"]): string {
  switch (kind) {
    case "pending":
      return "border-warning/60 bg-surface-raised";
    case "resolved":
      return "border-success/60 bg-surface-raised";
    case "skipped":
      return "border-subtle/50 bg-transparent";
    case "stale":
      return "border-subtle/50 bg-transparent";
    case "failed":
      return "border-danger/60 bg-surface-raised";
    default:
      // choosing / skipping — neutral, no color signal for transient
      // in-flight work.
      return "border-subtle/60 bg-surface-raised";
  }
}

function StatusIcon({ kind }: { kind: SelectionCardView["kind"] }) {
  switch (kind) {
    case "resolved":
      return <Check className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />;
    case "skipped":
      return <X className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />;
    case "stale":
      return <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />;
    case "failed":
      return <CircleX className="h-3.5 w-3.5 shrink-0 text-danger" aria-hidden="true" />;
    case "choosing":
    case "skipping":
      return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-tertiary" aria-hidden="true" />;
    default:
      return <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />; // pending
  }
}
