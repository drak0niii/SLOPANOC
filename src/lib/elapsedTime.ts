/** Compact, human-readable elapsed-time formatting for the active-run
 * indicator (interaction-capability extension). Purely a runtime-progress
 * display — unrelated to approval/proposal expiry, which remains
 * server-authoritative and never rendered as a countdown (see
 * ApprovalCard.tsx).
 *
 * Under 60s: "47s". At/above 60s: "1m 04s" (seconds always zero-padded to
 * two digits). No milliseconds. */
export function formatElapsedTime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
}

/** Whole seconds elapsed since `startedAt` (a `Date.now()`-style epoch ms
 * timestamp), floored, never negative. */
export function elapsedSecondsSince(startedAt: number, now: number = Date.now()): number {
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}
