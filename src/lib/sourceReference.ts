/**
 * Pure presentation helpers for the structured Teams source/provenance
 * drawer (pre-4H UX/provenance milestone) — see SourceChip.tsx for the
 * component that uses these. Kept separate/pure so date formatting is
 * independently testable without mounting anything.
 */

/** Short, readable date from an ISO timestamp string (e.g. "Aug 26") —
 * never throws on a malformed value, falling back to the raw string so a
 * display glitch never becomes a crash. */
export function formatShortIsoDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "<from> – <to>" for a source's reviewed time period, collapsing to a
 * single date when both ends land on the same day, and to just whichever
 * end is present if only one is known. `null` when neither is available
 * (nothing to show). */
export function formatSourcePeriod(start: string | null | undefined, end: string | null | undefined): string | null {
  const from = start ? formatShortIsoDate(start) : null;
  const to = end ? formatShortIsoDate(end) : null;
  if (from && to) return from === to ? from : `${from} – ${to}`;
  return from ?? to ?? null;
}

/** Date + time for one evidence entry's timestamp (e.g. "Aug 26, 9:00 AM")
 * — more precise than the period's date-only formatting, since individual
 * messages benefit from the time of day. Never throws on a malformed
 * value, falling back to the raw string. */
export function formatEvidenceTimestamp(sentAt: string): string {
  const date = new Date(sentAt);
  if (Number.isNaN(date.getTime())) return sentAt;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
