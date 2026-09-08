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

/** POST-5.1 B7 corrective pass — a concise, per-reference label for a
 * governed-KM Source chip, built entirely from trusted metadata the
 * backend already sends on `KnowledgeSourceReferenceDTO` (never re-
 * derived, never touching `source_uri`, which the DTO never carries at
 * all). Fixes the presentation defect where every KM chip on the same
 * answer read identically ("Source · Governed knowledge") even when they
 * were genuinely distinct evidence references (e.g. two different
 * sections of the same approved document) — the underlying references
 * were always correctly distinct; only the label failed to show it.
 *
 * Rule: "<title> · <section heading>" when both are present (the common,
 * most useful case); "<title>" alone when there is no section heading;
 * "<source display name>" if even `title` is somehow blank (defensive
 * only — the backend's own schema treats `title` as required/non-empty,
 * see backend/api/knowledge_source_reference.py); the original generic
 * "Governed knowledge" as the final fallback so a chip is never rendered
 * with empty/whitespace-only text.
 *
 * Deliberately NEVER deduplicates or merges references by title/
 * knowledge_id/section — two distinct selected evidence identities always
 * remain two distinct chips; this only changes what text each one shows. */
export function formatKnowledgeSourceLabel(source: {
  title: string;
  section_heading: string | null;
  source_display_name: string | null;
}): string {
  const title = source.title.trim();
  const heading = source.section_heading?.trim();
  if (title && heading) return `${title} · ${heading}`;
  if (title) return title;
  const displayName = source.source_display_name?.trim();
  if (displayName) return displayName;
  return "Governed knowledge";
}
