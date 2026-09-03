import { postJson } from "./client";
import type { ApprovalResponse, ExecuteActionResponse } from "./types";

/** Trusted approve of the session's current active proposal. Never
 * infers approval from conversation text — always an explicit
 * `session_id` + `proposal_id` pair, exactly matching what the backend
 * requires. Never executes anything by itself (see execution_service.py's
 * module docstring) — a separate `executeApprovedAction` call is needed
 * to actually perform the write. */
export async function approveAction(sessionId: string, proposalId: string): Promise<ApprovalResponse> {
  return postJson<ApprovalResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/approve`, {
    proposal_id: proposalId,
  });
}

/** Trusted reject of the session's current active proposal. Same
 * ownership/exactness guarantees as `approveAction`. */
export async function rejectAction(sessionId: string, proposalId: string): Promise<ApprovalResponse> {
  return postJson<ApprovalResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/reject`, {
    proposal_id: proposalId,
  });
}

/** The deterministic execution continuation for an ALREADY-approved
 * proposal (Phase 4G) — the only call that can actually perform the
 * Teams write. Never inferred from conversation text; never calls this
 * without a prior successful `approveAction`. */
export async function executeApprovedAction(sessionId: string, proposalId: string): Promise<ExecuteActionResponse> {
  return postJson<ExecuteActionResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/execute`, {
    proposal_id: proposalId,
  });
}
