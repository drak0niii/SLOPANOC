import { ApiError } from "../api/client";
import type { PendingActionDTO } from "../api/types";
import type { ApprovalCardState } from "../types";

export interface ApprovalCardView {
  kind: "pending" | "approving" | "executing" | "completed" | "rejecting" | "rejected" | "expired" | "unconfirmed" | "failed";
  message?: string;
  executedAction?: { chatId: string | null; title: string | null; webUrl: string | null } | null;
}

/**
 * The single place that reconciles the backend's durable
 * `pendingAction.status` with the frontend-only in-flight/terminal
 * `approvalCard.phase`. A `card` whose `proposalId` no longer matches
 * `pendingAction.proposal_id` (superseded by a newer proposal) is
 * ignored — the view falls back to deriving purely from the backend's
 * own current status.
 *
 * `status === "approved"` with no matching local card (e.g. after a page
 * reload, or after a live `executeApprovedAction` call whose outcome
 * could not be confirmed) NEVER renders as re-clickable "pending" —
 * "approved" is a distinct state from "pending", and since there is no
 * end-to-end write-idempotency guarantee, silently re-offering Approve
 * could risk a duplicate Teams write if the earlier attempt actually
 * succeeded but its confirmation was lost. It renders as `"unconfirmed"`
 * instead — non-actionable, explaining that execution status could not
 * be confirmed. `status === "consumed"` is NOT ambiguous the same way:
 * `consume_proposal` is only ever called after a confirmed Power
 * Automate success (see the frozen execute_write.py), so it safely maps
 * straight to `"completed"`.
 */
export function deriveApprovalCardView(
  pendingAction: PendingActionDTO,
  card: ApprovalCardState | null | undefined,
): ApprovalCardView {
  if (card && card.proposalId === pendingAction.proposal_id) {
    switch (card.phase) {
      case "approving":
        return { kind: "approving" };
      case "executing":
        return { kind: "executing" };
      case "completed":
        return { kind: "completed", executedAction: card.executedAction ?? null };
      case "rejecting":
        return { kind: "rejecting" };
      case "expired":
        return { kind: "expired", message: card.message };
      case "unconfirmed":
        return { kind: "unconfirmed", message: card.message };
      case "failed":
        return { kind: "failed", message: card.message };
    }
  }

  switch (pendingAction.status) {
    case "rejected":
      return { kind: "rejected" };
    case "expired":
      return { kind: "expired" };
    case "consumed":
      return { kind: "completed", executedAction: null };
    case "approved":
      // Distinct from "pending" — see this function's docstring. Never
      // re-clickable without a fresh, locally-tracked outcome.
      return { kind: "unconfirmed" };
    case "pending":
    default:
      return { kind: "pending" };
  }
}

const GENERIC_APPROVAL_ERROR = "Something went wrong. Please try again.";
const UNKNOWN_OUTCOME_MESSAGE = "We couldn't confirm whether this went through. Please check before trying again.";

/**
 * Classifies a thrown approve/reject/execute failure into a card phase —
 * using ONLY structured signals (`ApiError.reason`, the closed
 * `ApprovalDenialReason` vocabulary; `ApiError.errorCode`, the closed
 * backend `ErrorCode` vocabulary) — NEVER by matching `error.message`
 * prose, which is free text.
 *
 * - A structured `reason` of `"proposal_expired"` → `"expired"`; any
 *   other structured denial reason (stale/consumed/not-approved/
 *   rejected/no-pending) → `"failed"`, using the exact, specific backend
 *   message for that reason (already distinct and accurate — e.g. "This
 *   proposal is no longer the active one for this session").
 * - `errorCode === "rate_limited"` → `"failed"`: the gateway definitively
 *   responded and refused before any write could happen — a genuinely
 *   definite, provable failure.
 * - `errorCode === "run_failure"` or `"internal_error"` → `"unconfirmed"`:
 *   the frozen Power Automate gateway client collapses both a timeout and
 *   a definite HTTP-level rejection into these same codes, so there is no
 *   reliable signal here to prove the write never happened. Conservative
 *   by design — never claims a failure it cannot prove, and never claims
 *   success.
 * - Any other structured `errorCode` (e.g. a plain rejected/malformed
 *   request) → `"failed"`, a definite, provable rejection.
 * - No `ApiError` at all (a network-level failure that never reached our
 *   own backend, or reached it but produced no parseable response) →
 *   `"unconfirmed"` — we do not even know if the request was received.
 */
export function classifyApprovalFailure(error: unknown): { phase: "expired" | "failed" | "unconfirmed"; message: string } {
  if (error instanceof ApiError) {
    const message = error.message || GENERIC_APPROVAL_ERROR;
    if (error.reason) {
      return { phase: error.reason === "proposal_expired" ? "expired" : "failed", message };
    }
    if (error.errorCode === "run_failure" || error.errorCode === "internal_error") {
      return { phase: "unconfirmed", message: message || UNKNOWN_OUTCOME_MESSAGE };
    }
    return { phase: "failed", message };
  }
  return { phase: "unconfirmed", message: UNKNOWN_OUTCOME_MESSAGE };
}
