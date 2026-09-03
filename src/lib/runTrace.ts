import type { RunTraceStep } from "../types";
import { formatElapsedTime } from "./elapsedTime";

/**
 * Pure presentation helpers for the expandable, sanitized run trace
 * (pre-4H milestone) — see RunTrace.tsx for the component that uses
 * these. Kept separate/pure so the header-text and per-step icon logic
 * are independently testable without mounting anything.
 */

/** The completed trace's compact header line (instruction section 4 —
 * never "Thought for"/"Reasoned for"/anything chain-of-thought-flavored).
 * `outcome` is the run's own terminal outcome, never inferred from the
 * steps array. Both `"error"` (a genuine failure) and `"stopped"` (a
 * user-initiated abort) render the same "Stopped after Xs" text — the
 * user never needs to distinguish "it failed" from "I stopped it" at a
 * glance; only `"ok"` gets the success phrasing. */
export function formatCompletedTraceHeader(outcome: "ok" | "error" | "stopped", durationSeconds: number): string {
  const duration = formatElapsedTime(durationSeconds);
  return outcome === "ok" ? `Worked for ${duration}` : `Stopped after ${duration}`;
}

export type RunTraceStepIcon = "clock" | "warning" | "failed";

/** Which icon one step row renders — purely status-derived, deliberately
 * NOT positional: every `status: "completed"` step (including the final
 * one) gets the same neutral clock/activity marker, never a distinct
 * green success checkmark for "the last step" — this is presentation
 * only, not a claim that the final step is more significant than the
 * others. `warning`/`failed` still render their own distinct icon. */
export function deriveRunTraceStepIcon(step: RunTraceStep): RunTraceStepIcon {
  if (step.status === "warning") return "warning";
  if (step.status === "failed") return "failed";
  return "clock";
}
