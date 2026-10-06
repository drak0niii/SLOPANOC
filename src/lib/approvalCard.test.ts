import { describe, expect, it } from "vitest";
import { classifyApprovalFailure, deriveApprovalCardView } from "./approvalCard";
import { ApiError } from "../api/client";
import type { PendingActionDTO } from "../api/types";
import type { ApprovalCardState } from "../types";

function pendingAction(overrides: Partial<PendingActionDTO> = {}): PendingActionDTO {
  return {
    proposal_id: "p1",
    operation: "teams.sendMessage",
    status: "pending",
    summary: null,
    title: null,
    members: [],
    chat_id: null,
    message: null,
    expires_at: "2026-01-01T00:05:00Z",
    expires_in_seconds: 300,
    expires_in_minutes: 5,
    target_display_name: null,
    ...overrides,
  };
}

describe("deriveApprovalCardView — matching local card", () => {
  it.each([
    ["approving", "approving"],
    ["executing", "executing"],
    ["rejecting", "rejecting"],
  ] as const)("maps card.phase %s to view.kind %s", (phase, expectedKind) => {
    const card: ApprovalCardState = { proposalId: "p1", phase };
    const view = deriveApprovalCardView(pendingAction(), card);
    expect(view.kind).toBe(expectedKind);
  });

  it("maps completed with its executedAction", () => {
    const card: ApprovalCardState = {
      proposalId: "p1",
      phase: "completed",
      executedAction: { chatId: "c1", title: null, webUrl: "https://teams/x" },
    };
    const view = deriveApprovalCardView(pendingAction(), card);
    expect(view.kind).toBe("completed");
    expect(view.executedAction).toEqual({ chatId: "c1", title: null, webUrl: "https://teams/x" });
  });

  it("maps expired/unconfirmed/failed with their message", () => {
    for (const phase of ["expired", "unconfirmed", "failed"] as const) {
      const card: ApprovalCardState = { proposalId: "p1", phase, message: "exact backend message" };
      const view = deriveApprovalCardView(pendingAction(), card);
      expect(view.kind).toBe(phase);
      expect(view.message).toBe("exact backend message");
    }
  });

  it("ignores a stale card whose proposalId no longer matches", () => {
    const card: ApprovalCardState = { proposalId: "old-proposal", phase: "executing" };
    const view = deriveApprovalCardView(pendingAction({ proposal_id: "new-proposal", status: "pending" }), card);
    expect(view.kind).toBe("pending");
  });
});

describe("deriveApprovalCardView — no local card, derived from pendingAction.status alone", () => {
  it('"rejected" status -> rejected', () => {
    expect(deriveApprovalCardView(pendingAction({ status: "rejected" }), null).kind).toBe("rejected");
  });

  it('"expired" status -> expired', () => {
    expect(deriveApprovalCardView(pendingAction({ status: "expired" }), undefined).kind).toBe("expired");
  });

  it('"consumed" status -> completed (never ambiguous: only ever set after a confirmed success)', () => {
    const view = deriveApprovalCardView(pendingAction({ status: "consumed" }), null);
    expect(view.kind).toBe("completed");
    expect(view.executedAction).toBeNull();
  });

  it('"pending" status -> pending', () => {
    expect(deriveApprovalCardView(pendingAction({ status: "pending" }), null).kind).toBe("pending");
  });

  it('"approved" status -> unconfirmed, NEVER pending (adjustment: approved is a distinct state)', () => {
    const view = deriveApprovalCardView(pendingAction({ status: "approved" }), null);
    expect(view.kind).toBe("unconfirmed");
    expect(view.kind).not.toBe("pending");
  });
});

describe("classifyApprovalFailure — structured signals only, never message string-matching", () => {
  it('a structured reason of "proposal_expired" classifies as expired', () => {
    const error = new ApiError("This proposal has expired.", 409, {
      errorCode: "action_failure",
      reason: "proposal_expired",
    });
    expect(classifyApprovalFailure(error)).toEqual({ phase: "expired", message: "This proposal has expired." });
  });

  it("any other structured denial reason classifies as failed, using the exact backend message", () => {
    const error = new ApiError("This proposal is no longer the active one for this session.", 409, {
      errorCode: "action_failure",
      reason: "proposal_id_mismatch",
    });
    expect(classifyApprovalFailure(error)).toEqual({
      phase: "failed",
      message: "This proposal is no longer the active one for this session.",
    });
  });

  it('errorCode "rate_limited" (a definite, provable rejection) classifies as failed', () => {
    const error = new ApiError("Teams is temporarily rate-limiting requests.", 429, { errorCode: "rate_limited" });
    expect(classifyApprovalFailure(error).phase).toBe("failed");
  });

  it('errorCode "run_failure" (ambiguous transport outcome) classifies as unconfirmed, not failed', () => {
    const error = new ApiError("The Teams connector could not complete this request.", 502, {
      errorCode: "run_failure",
    });
    expect(classifyApprovalFailure(error).phase).toBe("unconfirmed");
  });

  it('errorCode "internal_error" also classifies as unconfirmed', () => {
    const error = new ApiError("Something went wrong.", 500, { errorCode: "internal_error" });
    expect(classifyApprovalFailure(error).phase).toBe("unconfirmed");
  });

  it("a network-level failure with no parsed ApiError at all classifies as unconfirmed", () => {
    const classification = classifyApprovalFailure(new TypeError("Failed to fetch"));
    expect(classification.phase).toBe("unconfirmed");
  });

  it("never inspects error.message text to decide the phase (only reason/errorCode)", () => {
    // A message that CONTAINS the word "expired" but carries no
    // structured `reason` must NOT be classified as expired.
    const error = new ApiError("Nothing here has expired, this is just prose.", 500, {
      errorCode: "internal_error",
    });
    expect(classifyApprovalFailure(error).phase).toBe("unconfirmed");
  });
});

describe("deriveApprovalCardView — Tranche 3 operational cards", () => {
  const operational = (overrides: Record<string, unknown> = {}) => ({
    kind: "approval" as const,
    control_id: "ctl-1",
    what: "Recovery",
    command: "acc PlugInUnit-3 restart",
    operation_type: "mutating_operational",
    risk: "state_changing" as const,
    target_type: "mo",
    target: "PlugInUnit-3",
    reason: "Governed step",
    source_title: "Ops MOP",
    source_section: "Node alarm",
    source_id: "K:v1:s",
    source_version: "v1",
    restrictions: [],
    control_stage: "awaiting_approval",
    approval_status: "pending",
    confirmed_by: "eng",
    approved_by: null,
    invalidation_reason: null,
    execution_note: "SLOPANOC never executes state-changing actions.",
    ...overrides,
  });

  it("approved operational action is 'ready' (never 'unconfirmed', never executing)", () => {
    const view = deriveApprovalCardView(
      pendingAction({ operation: "operational.procedureAction", status: "approved", operational: operational({ control_stage: "ready_for_execution" }) }),
      null,
    );
    expect(view.kind).toBe("ready");
  });

  it("invalidated operational action is a non-actionable failure with its reason", () => {
    const view = deriveApprovalCardView(
      pendingAction({ operation: "operational.procedureAction", status: "rejected", operational: operational({ control_stage: "invalidated", invalidation_reason: "binding_changed" }) }),
      null,
    );
    expect(view).toEqual({ kind: "failed", message: "This action is no longer valid (binding_changed)." });
  });

  it("pending operational action stays pending", () => {
    expect(deriveApprovalCardView(pendingAction({ operation: "operational.confirmTarget", operational: operational({ kind: "target_confirmation" }) }), null).kind).toBe("pending");
  });
});
