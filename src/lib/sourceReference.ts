/**
 * Pure presentation helpers for the structured Teams source/provenance
 * drawer (pre-4H UX/provenance milestone) — see SourceChip.tsx for the
 * component that uses these. Kept separate/pure so date formatting is
 * independently testable without mounting anything.
 */

import type { KnowledgeSourceReferenceDTO, SourceReferenceDTO } from "../api/types";

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

/** Teams Visual Evidence milestone — dynamic explanatory footer copy for
 * the Source drawer. When the answer also attached one or more real Teams
 * images to Gemini's visual input (`source.visual_evidence.length > 0`),
 * names both counts explicitly ("This response was derived from 1 Teams
 * message and 3 images retrieved from the conversation above."),
 * correctly pluralizing "message"/"image" independently. Falls back to
 * the original, unchanged generic sentence when there is no visual
 * evidence, OR (defensive only — `message_count` is always a real int
 * whenever a `SourceReferenceDTO` exists in practice, see backend/api/
 * source_reference.py's own `build_teams_source_reference`) when
 * `message_count` is somehow `null` — never a grammatically broken
 * sentence with a missing count. */
export function formatSourceFooter(source: SourceReferenceDTO): string {
  const genericFooter = "This response was derived from retrieved Teams messages in the conversation above.";
  if (source.visual_evidence.length === 0 || source.message_count == null) {
    return genericFooter;
  }
  const messageCount = source.message_count;
  const imageCount = source.visual_evidence.length;
  const messageWord = messageCount === 1 ? "message" : "messages";
  const imageWord = imageCount === 1 ? "image" : "images";
  return `This response was derived from ${messageCount} Teams ${messageWord} and ${imageCount} ${imageWord} retrieved from the conversation above.`;
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

/** POST-A5 refinement (Track B, final corrective pass) — one consolidated
 * presentation group per DISTINCT governed source+version, built entirely
 * by aggregating already-trusted `KnowledgeSourceReferenceDTO` entries
 * (never re-derived from the backend, never mutating the originals).
 * `sections` preserves each distinct selected section verbatim — this is
 * presentation-only aggregation over already-trusted per-section
 * provenance, not a new provenance shape; SEARCH RESULT != EVIDENCE USED
 * still holds because every entry here still traces back to one real
 * `KnowledgeSourceReferenceDTO` the backend actually sent. */
export interface KnowledgeSourceGroup {
  /** Stable grouping identity — `knowledge_id` + `version_label` +
   * `evidence_source_id` (backend/api/knowledge_source_reference.py's own
   * `KnowledgeSource.source_id`: "the identifier of this content within
   * source_system" — the authoritative identity of the underlying source
   * FILE, not a display label). Audited against `KnowledgeObject`
   * (backend/knowledge/domain/models.py): `source` is a single,
   * OBJECT-level field (one `KnowledgeSource` per `knowledge_id`+
   * `version_label`, shared by every section and artifact of that
   * object) — so `evidence_source_id` can never legitimately vary within
   * one `(knowledge_id, version_label)` pair for real backend data;
   * including it is a defensive strengthening of the identity (so two
   * references that somehow claimed the same knowledge_id/version but a
   * genuinely different source file would still never be silently
   * merged), not a behavior change for correctly-formed data. Deliberately
   * never `source_id` (a synthetic per-DTO `uuid4`, not a stable identity
   * — a completely different field from `evidence_source_id` despite the
   * similar name), never title/section-heading/display-name/content
   * (which can legitimately collide across genuinely distinct sources). */
  groupKey: string;
  knowledgeId: string;
  versionLabel: string;
  title: string;
  documentType: string;
  sourceSystem: string;
  evidenceSourceId: string;
  sourceDisplayName: string | null;
  /** Every distinct selected section belonging to this source+version,
   * deduplicated by `section_id` ONLY (never heading/content), first-seen
   * order preserved — two sections sharing an identical heading still both
   * survive as long as their `section_id` differs. */
  sections: KnowledgeSourceReferenceDTO[];
}

/** Groups an already-trusted, already-ordered list of
 * `KnowledgeSourceReferenceDTO` entries into one `KnowledgeSourceGroup` per
 * distinct `(knowledge_id, version_label, evidence_source_id)` triple,
 * preserving first-seen group order and first-seen section order within
 * each group. A DIFFERENT `knowledge_id`, a DIFFERENT `version_label`, OR a
 * DIFFERENT `evidence_source_id` (even for the same title/document display
 * name) always produces a SEPARATE group — title/display name are
 * display-only fields on the DTO, never consulted for identity. Pure and
 * deterministic: safe to call on freshly-arrived live SSE data or on
 * rehydrated historical data alike, and safe to call again on every render
 * (no random ids, no hidden state). Returns `[]` for `undefined`/an empty
 * input, never a fabricated placeholder group. */
export function groupKnowledgeSourceReferences(
  references: KnowledgeSourceReferenceDTO[] | undefined,
): KnowledgeSourceGroup[] {
  if (!references || references.length === 0) return [];

  const groups: KnowledgeSourceGroup[] = [];
  const groupByKey = new Map<string, KnowledgeSourceGroup>();

  for (const reference of references) {
    const groupKey = `${reference.knowledge_id}::${reference.version_label}::${reference.evidence_source_id}`;
    let group = groupByKey.get(groupKey);
    if (!group) {
      group = {
        groupKey,
        knowledgeId: reference.knowledge_id,
        versionLabel: reference.version_label,
        title: reference.title,
        documentType: reference.document_type,
        sourceSystem: reference.source_system,
        evidenceSourceId: reference.evidence_source_id,
        sourceDisplayName: reference.source_display_name,
        sections: [],
      };
      groupByKey.set(groupKey, group);
      groups.push(group);
    }
    if (!group.sections.some((existing) => existing.section_id === reference.section_id)) {
      group.sections.push(reference);
    }
  }

  return groups;
}

/** Message-level chip label for a consolidated source+version group —
 * "<title> · <version_label>" (e.g. "Aurora Relay Verification Procedure ·
 * v1"), falling back to `sourceDisplayName` and finally the generic
 * "Governed knowledge" exactly like `formatKnowledgeSourceLabel`'s own
 * fallback chain, always still suffixed with the version so the version is
 * visible on every chip regardless of which title fallback applies. */
export function formatKnowledgeSourceGroupLabel(group: KnowledgeSourceGroup): string {
  const title = group.title.trim();
  if (title) return `${title} · ${group.versionLabel}`;
  const displayName = group.sourceDisplayName?.trim();
  if (displayName) return `${displayName} · ${group.versionLabel}`;
  return `Governed knowledge · ${group.versionLabel}`;
}
