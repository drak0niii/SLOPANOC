import type {
  DestinationKindId,
  SourceKindId,
  TaskSource,
} from "../types";

/**
 * The single catalogue of typed endpoints available inside connectors.
 *
 * Deliberately one list rather than three: a scheduled task *reads* several of
 * these on a cadence, a chat *reads* one ad hoc, and a distribution *writes*
 * to one. Same primitive, three triggers.
 *
 * No icons live here — components own their own icon maps, matching how
 * ScheduledTasksPage already keeps its template icons out of `data/`.
 */
export interface SourceKind {
  id: SourceKindId;
  /** Full name, used in the editor's kind select. */
  label: string;
  /** Compact name, used in run output headings. */
  shortLabel: string;
  connectorId: string;
  /** What `scope` means for this kind — labels the second select. */
  scopeLabel: string;
  /** Every option is backed by fixture content, so a run always finds data. */
  scopeOptions: string[];
  writable: boolean;
}

export const SOURCE_KINDS: SourceKind[] = [
  {
    id: "outlook_calendar",
    label: "Outlook calendar",
    shortLabel: "Calendar",
    connectorId: "connector-outlook",
    scopeLabel: "Range",
    scopeOptions: ["Today", "Tomorrow", "This week"],
    writable: false,
  },
  {
    id: "outlook_inbox",
    label: "Outlook inbox",
    shortLabel: "Inbox",
    connectorId: "connector-outlook",
    scopeLabel: "Folder",
    scopeOptions: ["Inbox", "NOC Handover", "Escalations"],
    writable: false,
  },
  {
    id: "teams_channel",
    label: "Teams channel",
    shortLabel: "Teams",
    connectorId: "connector-teams",
    scopeLabel: "Channel",
    scopeOptions: ["Ops Bridge", "NOC Handover", "Change Management"],
    writable: true,
  },
  {
    id: "sharepoint_folder",
    label: "SharePoint folder",
    shortLabel: "SharePoint",
    connectorId: "connector-sharepoint",
    scopeLabel: "Folder",
    scopeOptions: ["Runbooks", "Post-incident reviews"],
    writable: false,
  },
];

export const SOURCE_KIND_BY_ID = Object.fromEntries(
  SOURCE_KINDS.map((kind) => [kind.id, kind]),
) as Record<SourceKindId, SourceKind>;

/** Kinds a chat can be pointed at from the composer's "+" menu. */
export const CHAT_ROOM_KIND_IDS: SourceKindId[] = ["teams_channel"];

export function formatSourceLabel(source: TaskSource): string {
  const kind = SOURCE_KIND_BY_ID[source.kind];
  if (!kind) return source.scope;
  return `${kind.shortLabel} · ${source.scope}`;
}

/**
 * A writable endpoint, flattened for the "Send to…" menu and the task's
 * distribution picker. Email is not a source kind (there is nothing to read
 * from an arbitrary address), so it is appended separately.
 */
export interface DestinationOption {
  /** Stable composite id, e.g. "teams_channel:Ops Bridge". */
  id: string;
  kind: DestinationKindId;
  target: string;
  /** Display form — "#Ops Bridge", "ops@company.com". */
  label: string;
  connectorId: string;
}

const CHANNEL_DESTINATIONS: DestinationOption[] = SOURCE_KINDS.filter(
  (kind) => kind.writable,
).flatMap((kind) =>
  kind.scopeOptions.map((scope) => ({
    id: `${kind.id}:${scope}`,
    kind: kind.id as DestinationKindId,
    target: scope,
    label: scope.startsWith("#") ? scope : `#${scope}`,
    connectorId: kind.connectorId,
  })),
);

const EMAIL_DESTINATIONS: DestinationOption[] = [
  "noc-leads@company.com",
  "service-desk@company.com",
].map((address) => ({
  id: `email:${address}`,
  kind: "email" as const,
  target: address,
  label: address,
  connectorId: "connector-outlook",
}));

export const DESTINATION_OPTIONS: DestinationOption[] = [
  ...CHANNEL_DESTINATIONS,
  ...EMAIL_DESTINATIONS,
];

export function findDestination(id: string): DestinationOption | undefined {
  return DESTINATION_OPTIONS.find((option) => option.id === id);
}

export function findDestinationByValue(
  kind: DestinationKindId,
  target: string,
): DestinationOption | undefined {
  return DESTINATION_OPTIONS.find((option) => option.kind === kind && option.target === target);
}
