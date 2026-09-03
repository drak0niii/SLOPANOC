import { postJson } from "./client";
import type { ChooseSelectionResponse, SkipSelectionResponse } from "./types";

/** Trusted choice of one candidate for the session's current active
 * Teams chat selection. Never infers a choice from conversation text —
 * always an explicit `session_id`/`selection_id`/`option_id` triple,
 * exactly matching what the backend requires. Selecting a candidate is
 * NOT itself an approval: for a pending WRITE, this only produces a
 * normal `ActionProposal` (see `ChooseSelectionResponse.pending_action`)
 * — the user must still separately approve it via the existing
 * approve/execute flow. */
export async function chooseSelection(
  sessionId: string,
  selectionId: string,
  optionId: string,
): Promise<ChooseSelectionResponse> {
  return postJson<ChooseSelectionResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/selections/${encodeURIComponent(selectionId)}/choose`,
    { option_id: optionId },
  );
}

/** Trusted skip of the session's current active Teams chat selection —
 * no candidate is chosen, no proposal is created, nothing is sent. */
export async function skipSelection(sessionId: string, selectionId: string): Promise<SkipSelectionResponse> {
  return postJson<SkipSelectionResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/selections/${encodeURIComponent(selectionId)}/skip`,
  );
}
