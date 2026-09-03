/**
 * Deterministic operation -> button-label mapping for ApprovalCard (Phase
 * 4G hardening pass). The primary button describes the action itself
 * ("Create", "Send") rather than a generic "Approve" — derived ONLY from
 * the structured `operation` field the backend reports, never inferred
 * from generated assistant prose or any other natural-language text.
 *
 * Adding a future operation is a one-line addition to the map below; an
 * operation this map doesn't recognize falls back to a safe generic
 * label rather than guessing.
 */
export interface ActionOperationPresentation {
  /** Label for the primary approve-and-perform button. */
  primaryButtonLabel: string;
}

const ACTION_OPERATION_PRESENTATION: Record<string, ActionOperationPresentation> = {
  "teams.createChat": { primaryButtonLabel: "Create" },
  "teams.sendMessage": { primaryButtonLabel: "Send" },
};

const FALLBACK_PRESENTATION: ActionOperationPresentation = { primaryButtonLabel: "Approve" };

export function getActionOperationPresentation(operation: string): ActionOperationPresentation {
  return ACTION_OPERATION_PRESENTATION[operation] ?? FALLBACK_PRESENTATION;
}
