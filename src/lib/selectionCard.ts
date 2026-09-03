import { ApiError } from "../api/client";
import type { SelectionCardState } from "../types";

export interface SelectionCardView {
  kind: "pending" | "choosing" | "skipping" | "resolved" | "skipped" | "stale" | "failed";
  message?: string;
  /** Only for "resolved" — the human-readable label of the chosen
   * candidate, exactly as the backend returned it. */
  selectedLabel?: string;
}

/**
 * The single place that reconciles a selection card's frontend-only
 * in-flight/terminal `SelectionCardState` with whether it is still the
 * chat's CURRENT selection — mirrors `deriveApprovalCardView`, but a
 * selection has no backend-durable terminal status to fall back to on
 * its own frozen DTO snapshot (see `Chat.selectionCards` docstring):
 * `record.pendingSelection.status` is always "pending" unless a local
 * `selectionCard` state says otherwise, because the backend never
 * re-emits a fresh event for an already-resolved/skipped/superseded
 * selection.
 *
 * `isCurrent` — whether `record.selectionId` still matches
 * `chat.pendingSelection?.selection_id` — is what makes a stale card
 * (superseded by the conversation moving on, e.g. an exact-match
 * resolution elsewhere) render as non-actionable "stale" instead of a
 * forever-clickable "pending" card, even though no local `selectionCard`
 * state was ever set for it.
 */
export function deriveSelectionCardView(
  isCurrent: boolean,
  cardState: SelectionCardState | null | undefined,
): SelectionCardView {
  switch (cardState?.phase) {
    case "choosing":
      return { kind: "choosing" };
    case "skipping":
      return { kind: "skipping" };
    case "resolved":
      return { kind: "resolved", selectedLabel: cardState.selectedLabel };
    case "skipped":
      return { kind: "skipped" };
    case "failed":
      return { kind: "failed", message: cardState.message };
    default:
      return { kind: isCurrent ? "pending" : "stale" };
  }
}

const GENERIC_SELECTION_ERROR = "Something went wrong. Please try again.";

/**
 * Classifies a thrown choose/skip failure — using only structured
 * signals (`ApiError.reason`, the closed `SelectionDenialReason`
 * vocabulary), never by matching `error.message` prose. Every denial
 * reason here is a definite, provable rejection (unlike the approval
 * flow's execute path, a selection choice has no external side effect of
 * its own — see selection_service.py's module docstring — so there is no
 * "unconfirmed" state to consider).
 */
export function classifySelectionFailure(error: unknown): { message: string } {
  if (error instanceof ApiError) {
    return { message: error.message || GENERIC_SELECTION_ERROR };
  }
  return { message: GENERIC_SELECTION_ERROR };
}
