import { FileText, MessagesSquare, NotebookText } from "../ui/icons";
import type { Citation } from "../../types";
import type { KnowledgeSourceReferenceDTO, SourceReferenceDTO } from "../../api/types";
import { DrawerContent, DrawerRoot, DrawerTitle, DrawerTrigger } from "../ui/Drawer";
import { formatEvidenceTimestamp, formatKnowledgeSourceLabel, formatSourcePeriod } from "../../lib/sourceReference";

/**
 * The shared source/provenance affordance (pre-4H UX/provenance
 * milestone) — a compact clickable chip that opens the SAME right-side
 * drawer shell (Drawer.tsx's Radix-based primitives, unchanged) already
 * used for MOP document citations. `SourceCitation.tsx` is now a thin
 * wrapper around this component (`kind: "mop"`) — its own external API
 * and rendered output are unchanged, so no existing MOP call site or
 * behavior regresses. `kind: "teams"` is the new branch: safe, already-
 * structured Teams provenance (see backend/api/source_reference.py),
 * NEVER built by parsing the assistant's own answer text.
 *
 * Purely presentation/navigation — opening/closing is local, uncontrolled
 * Radix Dialog state (mirrors SourceCitation's own prior behavior
 * exactly): never calls the backend, never reruns retrieval, never
 * mutates session state.
 */
export type SourceChipProps =
  | { kind: "mop"; citation: Citation }
  | { kind: "teams"; source: SourceReferenceDTO }
  | { kind: "knowledge"; source: KnowledgeSourceReferenceDTO };

// MOP's trigger is unchanged byte-for-byte from the pre-refactor
// SourceCitation.tsx — no visual regression for existing MOP call sites.
const MOP_TRIGGER_CLASSES =
  "inline-flex items-center gap-1 rounded-md bg-surface-hover/60 px-1.5 py-0.5 text-xs font-medium text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";

// UX polish pass: a slightly larger, slightly more present trigger for the
// Teams source affordance specifically (more horizontal/vertical padding,
// a touch more breathing room and background) — MOP's own trigger above is
// deliberately left untouched.
const TEAMS_TRIGGER_CLASSES =
  "inline-flex items-center gap-1.5 rounded-lg bg-surface-hover/70 px-2.5 py-1.5 text-xs font-medium text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";

export function SourceChip(props: SourceChipProps) {
  return (
    <DrawerRoot>
      <DrawerTrigger asChild>
        <button type="button" className={props.kind === "mop" ? MOP_TRIGGER_CLASSES : TEAMS_TRIGGER_CLASSES}>
          {props.kind === "mop" && (
            <>
              <FileText className="h-2.5 w-2.5" />
              {props.citation.docId}
            </>
          )}
          {props.kind === "teams" && (
            <>
              <MessagesSquare className="h-3 w-3" />
              Source · {props.source.label}
            </>
          )}
          {props.kind === "knowledge" && (
            <>
              <NotebookText className="h-3 w-3" />
              Source · {formatKnowledgeSourceLabel(props.source)}
            </>
          )}
        </button>
      </DrawerTrigger>
      <DrawerContent className="flex flex-col overflow-y-auto p-5">
        {props.kind === "mop" && <MopSourceDetails citation={props.citation} />}
        {props.kind === "teams" && <TeamsSourceDetails source={props.source} />}
        {props.kind === "knowledge" && <KnowledgeSourceDetails source={props.source} />}
      </DrawerContent>
    </DrawerRoot>
  );
}

/** Unchanged from the pre-existing SourceCitation.tsx — moved here
 * verbatim so MOP rendering is byte-identical to before this refactor. */
function MopSourceDetails({ citation }: { citation: Citation }) {
  return (
    <>
      <DrawerTitle>{citation.docTitle}</DrawerTitle>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="rounded-full bg-success/10 px-2 py-0.5 font-medium text-success">Approved</span>
        <span className="text-tertiary">{citation.version}</span>
        <span className="text-tertiary">·</span>
        <span className="text-tertiary">{citation.docId}</span>
      </div>

      <p className="mt-1.5 text-sm text-tertiary">{citation.scopeLabel}</p>

      <div className="mt-5 text-sm font-medium text-tertiary">Section {citation.section}</div>

      <div className="mt-4">
        <p className="text-sm font-medium text-tertiary">Relevant excerpt</p>
        <p className="mt-2 border-l-2 border-accent/40 pl-3 text-base italic leading-relaxed text-secondary">
          "{citation.excerpt}"
        </p>
      </div>
    </>
  );
}

// Drawer must stay compact and scannable, not a full transcript — cap the
// rendered examples even though the backend already caps `evidence` itself
// at the same limit (defense in depth, mirrors the backend's own "don't
// trust a single layer" posture).
const MAX_RENDERED_EVIDENCE_ITEMS = 5;

/** Snippet-authenticity fix: the backend is the primary enforcement point
 * (it never even constructs a `SourceEvidenceItem` without a valid, non-
 * empty snippet — see backend/api/source_reference.py), but this frontend
 * filter is a second, defensive layer — an evidence item with no usable
 * snippet is skipped entirely, never rendered as an empty author/timestamp
 * -only row. Applied BEFORE the display cap, so a malformed item never
 * displaces a valid one from the visible 5. */
function hasDisplayableSnippet(item: { snippet?: string | null }): item is { snippet: string } {
  return typeof item.snippet === "string" && item.snippet.trim().length > 0;
}

/** Teams provenance drawer content (pre-4H UX/provenance milestone) --
 * every field here is already-safe, already-structured data from
 * `SourceReferenceDTO` (see that DTO's own docstring for what it
 * deliberately excludes — raw chat_id, message ids, any credential).
 * Fields that weren't authoritative for this particular answer (no
 * known period, no known contributors) are simply omitted, never shown
 * as a placeholder/guess. */
function TeamsSourceDetails({ source }: { source: SourceReferenceDTO }) {
  const period = formatSourcePeriod(source.period_start, source.period_end);
  const evidence = source.evidence.filter(hasDisplayableSnippet).slice(0, MAX_RENDERED_EVIDENCE_ITEMS);

  return (
    <>
      <DrawerTitle>Source</DrawerTitle>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="rounded-full bg-info/10 px-2 py-0.5 font-medium text-info">Microsoft Teams</span>
      </div>

      {source.title && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Conversation</p>
          <p className="mt-1 text-sm text-secondary">{source.title}</p>
        </div>
      )}

      {(source.message_count != null || period) && (
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3">
          {source.message_count != null && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Messages reviewed</p>
              <p className="mt-1 text-sm text-secondary">{source.message_count}</p>
            </div>
          )}
          {period && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Period</p>
              <p className="mt-1 text-sm text-secondary">{period}</p>
            </div>
          )}
        </div>
      )}

      {source.contributors.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Contributors</p>
          <p className="mt-1 text-sm text-secondary">{source.contributors.join(", ")}</p>
        </div>
      )}

      <p className="mt-5 text-sm text-tertiary">
        This response was derived from retrieved Teams messages in the conversation above.
      </p>

      {evidence.length > 0 && (
        <div className="mt-5">
          <p className="text-sm font-medium text-tertiary">Supporting evidence</p>
          <ul className="mt-2 flex flex-col gap-2.5">
            {evidence.map((item, index) => (
              <li key={index} className="border-l-2 border-accent/40 pl-3 text-sm leading-relaxed text-secondary">
                <div>
                  <span className="font-medium text-primary">{item.author}</span>{" "}
                  <span className="text-tertiary">· {formatEvidenceTimestamp(item.sent_at)}</span>
                </div>
                {/* `hasDisplayableSnippet` already guarantees this, but the
                    check is kept so a future refactor can never silently
                    reintroduce an empty/author-only row here. */}
                {item.snippet && <p className="mt-1 italic text-secondary/90">"{item.snippet}"</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/** Human-readable label for a governed `document_type` value — purely a
 * display label; carries no ranking/priority meaning (Generic KM treats
 * every document type identically — docs/KNOWLEDGE_CONTRACT.md). Falls
 * back to the raw value itself for any type this build doesn't have a
 * specific label for, so a future/unknown type still renders sensibly. */
function formatDocumentType(documentType: string): string {
  const labels: Record<string, string> = {
    mop: "Method of Procedure",
    sop: "Standard Operating Procedure",
    rca: "Root Cause Analysis",
    kb_article: "KB Article",
    troubleshooting_guide: "Troubleshooting Guide",
    operational_procedure: "Operational Procedure",
    technical_instruction: "Technical Instruction",
    other: "Document",
  };
  return labels[documentType] ?? documentType;
}

/** Governed-knowledge provenance drawer content (Phase 5.1J correction
 * pass, Part C) — every field here is already-safe, already-structured
 * data from `KnowledgeSourceReferenceDTO`, built entirely from a trusted
 * backend `KnowledgeEvidenceItem` the model explicitly SELECTED this
 * turn (never every item `knowledge_search` merely returned). Never
 * shows `source_uri` — deliberately absent from the DTO itself, not just
 * hidden here (see docs/KNOWLEDGE_CONTRACT.md's Phase 5.1J section). */
function KnowledgeSourceDetails({ source }: { source: KnowledgeSourceReferenceDTO }) {
  return (
    <>
      <DrawerTitle>{source.title}</DrawerTitle>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <span className="rounded-full bg-accent/10 px-2 py-0.5 font-medium text-accent">
          {formatDocumentType(source.document_type)}
        </span>
        <span className="text-tertiary">·</span>
        <span className="text-tertiary">{source.version_label}</span>
      </div>

      {source.section_heading && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Section</p>
          <p className="mt-1 text-sm text-secondary">{source.section_heading}</p>
        </div>
      )}

      <div className="mt-4">
        <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Source</p>
        <p className="mt-1 text-sm text-secondary">
          {source.source_display_name ?? source.source_system}
          {source.source_display_name ? <span className="text-tertiary"> · {source.source_system}</span> : null}
        </p>
        <p className="text-sm text-tertiary">{source.evidence_source_id}</p>
        {source.source_locator && <p className="text-sm text-tertiary">{source.source_locator}</p>}
      </div>

      <div className="mt-5">
        <p className="text-sm font-medium text-tertiary">Supporting evidence</p>
        <p className="mt-2 border-l-2 border-accent/40 pl-3 text-sm italic leading-relaxed text-secondary">
          "{source.content}"
        </p>
      </div>
    </>
  );
}
