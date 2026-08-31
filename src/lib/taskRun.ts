import type {
  Connector,
  ScheduledTask,
  ScheduledTaskFrequency,
  TaskSource,
} from "../types";
import {
  SOURCE_KIND_BY_ID,
  type SourceKind,
} from "../data/workspaceSources";
import {
  BRIEF_STYLE,
  CALENDAR_EVENTS,
  CHANNEL_MESSAGES,
  DEFAULT_BRIEF_STYLE,
  DEFAULT_RUN_STYLE,
  INBOX_ITEMS,
  RUN_STYLE,
  SHAREPOINT_DOCS,
  type RunToneId,
} from "../data/workspaceFixtures";

/**
 * Turns a set of endpoints into prose. Shared by scheduled runs and by the
 * composer's ad-hoc "read this chat room" flow — the two features differ only
 * in what triggers them and how the result is framed.
 *
 * Everything here is deterministic: the same sources always produce the same
 * text, so a demo never drifts.
 */

export interface SourceRead {
  source: TaskSource;
  kind: SourceKind;
  /** Whether the backing connector is currently connected. */
  available: boolean;
  headline: string;
  lines: string[];
  /** Promoted into a ```finding block — the one thing worth acting on. */
  urgent?: string;
}

export function readSources(
  sources: TaskSource[],
  connectors: Record<string, Connector>,
): SourceRead[] {
  return sources.map((source) => {
    const kind = SOURCE_KIND_BY_ID[source.kind];
    const available = connectors[kind?.connectorId ?? ""]?.state === "connected";

    if (!kind) {
      return { source, kind: kind!, available: false, headline: source.scope, lines: [] };
    }
    if (!available) {
      return {
        source,
        kind,
        available: false,
        headline: `${kind.shortLabel} — ${kind.label} isn't connected, skipped`,
        lines: [],
      };
    }

    return { source, kind, available: true, ...readContent(source, kind) };
  });
}

function readContent(
  source: TaskSource,
  kind: SourceKind,
): { headline: string; lines: string[]; urgent?: string } {
  switch (source.kind) {
    case "outlook_calendar": {
      const events = CALENDAR_EVENTS[source.scope] ?? [];
      return {
        headline: `Calendar — ${countLabel(events.length, "meeting")}`,
        lines: events.map(
          (event) =>
            `• ${event.timeLabel}  ${event.title} — ${event.attendees}${
              event.note ? ` (${event.note})` : ""
            }`,
        ),
      };
    }
    case "outlook_inbox": {
      const items = INBOX_ITEMS[source.scope] ?? [];
      const urgent = items.find((item) => item.urgent);
      return {
        headline: `Inbox · ${source.scope} — ${countLabel(items.length, "message")}`,
        // `preview` stays on the urgent finding below rather than every line,
        // so a digest doesn't become a wall of text.
        lines: items.map((item) => `• ${item.receivedLabel}  ${item.from} — ${item.subject}`),
        urgent: urgent ? `${urgent.subject} — ${urgent.preview}` : undefined,
      };
    }
    case "teams_channel": {
      const messages = CHANNEL_MESSAGES[source.scope] ?? [];
      const last = messages[messages.length - 1];
      return {
        headline: `${kind.shortLabel} · ${source.scope} — ${countLabel(messages.length, "message")}`,
        lines: messages.slice(-3).map((m) => `• ${m.author} (${m.timeLabel}): ${m.text}`),
        urgent: last ? `Latest from ${source.scope}: ${last.text}` : undefined,
      };
    }
    case "sharepoint_folder": {
      const docs = SHAREPOINT_DOCS[source.scope] ?? [];
      return {
        headline: `SharePoint · ${source.scope} — ${countLabel(docs.length, "document")}`,
        lines: docs.map((doc) => `• ${doc.title} — ${doc.note}`),
      };
    }
    default:
      return { headline: source.scope, lines: [] };
  }
}

function countLabel(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** Connector ids for sources that couldn't be read — drives the existing
 * ConnectorSuggestionsCard so the user can fix a thin brief in one click. */
export function skippedConnectorIds(reads: SourceRead[]): string[] {
  return Array.from(
    new Set(reads.filter((read) => !read.available && read.kind).map((read) => read.kind.connectorId)),
  );
}

/** What a run of this cadence is called. Used instead of the task's name,
 * which for agent-created tasks is the opening prompt truncated to 48 chars
 * — so the old opening line read "Here's your read my outlook calendar for
 * today, my noc handov… for Monday". The name is already shown three times
 * over: the chat title, the detail-page headline, and the dateline above
 * this very message. */
const RUN_NOUN: Record<ScheduledTaskFrequency, string> = {
  daily: "daily update",
  weekdays: "weekday update",
  weekly: "weekly update",
  monthly: "monthly update",
  manual: "update",
};

/** Very small, deliberately naive heuristics — the same approach as
 * `guessSources` in data/mock.ts. Enough to make a task's own words visibly
 * change its brief without pretending to understand them. */
const URGENCY_PATTERN =
  /\b(urgent|urgency|escalat\w*|critical|blocker|overdue|breach|outage|incident|sev-?[12]|p1|asap|immediately|priorit\w+|risks?|needs?\s+(my\s+)?attention|anything\s+that\s+needs)\b/;

/** Narrow on purpose: a bare "brief" would match "morning brief" and "daily
 * briefing", which ask for a digest, not headlines. */
const BREVITY_PATTERN =
  /\b(keep\s+it\s+(short|brief|tight)|short\s+(brief|version|summary)|briefly|concise|scannable|headlines?|bullet\s*points?|tl;?dr|one[-\s]liner|just\s+the\s+(headlines|highlights|key\s+points))\b/;

function guessRunTone(instructions: string): RunToneId {
  const lower = instructions.toLowerCase();
  const urgent = URGENCY_PATTERN.test(lower);
  const short = BREVITY_PATTERN.test(lower);
  if (urgent && short) return "urgent-short";
  if (urgent) return "urgent";
  if (short) return "short";
  return "digest";
}

/** "Run · Mon 25 Aug, 08:00" — goes into Message.speakerLabel, where the
 * existing uppercase/tracked styling reads as a dateline. */
export function formatRunLabel(runAt: number): string {
  return `Run · ${new Date(runAt).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

/**
 * A scheduled run's message. Prose-first — it's a chat message, not a
 * dashboard — using only fenced tags Message.tsx already understands.
 */
export function composeRunMessage(
  task: ScheduledTask,
  reads: SourceRead[],
  runAt: number,
): { text: string; summary: string } {
  const dateLabel = new Date(runAt).toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  // The task's own instructions decide how this reads — see guessRunTone.
  const style = RUN_STYLE[guessRunTone(task.instructions)] ?? DEFAULT_RUN_STYLE;
  const parts: string[] = [`Here's your ${RUN_NOUN[task.frequency]} for ${dateLabel}.`];

  if (reads.length === 0) {
    parts.push(
      "No sources are configured yet, so there was nothing to read. Add a source on the task to give this something to work from.",
    );
    return { text: parts.join("\n\n"), summary: "No sources configured" };
  }

  parts.push(style.lead);

  const urgent = reads.find((read) => read.urgent)?.urgent;

  // "Promote" is literal: under an urgency-led instruction the finding moves
  // above the source sections, and its absence is stated rather than implied
  // — silence would otherwise be indistinguishable from nothing being found.
  if (style.urgencyFirst) {
    parts.push(
      urgent
        ? `\`\`\`finding\n${urgent}\n\`\`\``
        : "```success\nNothing urgent across the sources read.\n```",
    );
  }

  for (const read of reads) {
    parts.push(style.headlinesOnly ? read.headline : [read.headline, ...read.lines].join("\n"));
  }

  if (!style.urgencyFirst && urgent) {
    parts.push(`\`\`\`finding\n${urgent}\n\`\`\``);
  }

  const unavailable = reads.filter((read) => !read.available);
  if (unavailable.length > 0 && unavailable.length < reads.length) {
    parts.push(
      `${unavailable.length} of ${reads.length} sources couldn't be read. Connect them to get the full picture.`,
    );
  }

  parts.push(`\`\`\`cta\nAsk a follow-up|${style.closing}\n\`\`\``);

  const available = reads.filter((read) => read.available);
  const summary =
    available.length === 0
      ? "No sources could be read"
      : `${available.map((read) => read.kind.shortLabel).join(", ")} reviewed`;

  return { text: parts.join("\n\n"), summary };
}

/**
 * The ad-hoc equivalent: the user pointed the assistant at one or more rooms
 * from the composer. Same reads, framed as a situation summary plus next
 * actions, styled by whichever skill the chat has active.
 */
export function composeSourceBrief(
  reads: SourceRead[],
  promptText: string,
  activeSkillId: string | null,
): string {
  const style = (activeSkillId && BRIEF_STYLE[activeSkillId]) || DEFAULT_BRIEF_STYLE;
  const parts: string[] = [];

  const unavailable = reads.filter((read) => !read.available);
  if (unavailable.length === reads.length) {
    return [
      "I couldn't read any of the sources you attached — none of their connectors are connected.",
      "Connect them and send this again and I'll summarise what's there.",
    ].join("\n\n");
  }

  // Show the raw transcript first, so it's clear what was actually read
  // rather than asking the user to trust a summary.
  const room = reads.find(
    (read) => read.available && read.source.kind === "teams_channel",
  );
  if (room) {
    const messages = CHANNEL_MESSAGES[room.source.scope] ?? [];
    const excerpt = messages
      .slice(-8)
      .map((m) => `${m.timeLabel}  ${m.author}: ${m.text}`)
      .join("\n");
    parts.push(`\`\`\`output\n${excerpt}\n\`\`\``);
  }

  parts.push(style.lead);

  for (const read of reads.filter((read) => read.available)) {
    parts.push([read.headline, ...read.lines].join("\n"));
  }

  if (room) {
    parts.push(
      "```finding\nSITE-102 Sector A has been down since 02:14. Fronthaul, power and hardware are all healthy, and no change is active — the fault is isolated to RU-A1 being unresponsive.\n```",
    );
    parts.push(
      `${style.actionsHeading}:\n\n` +
        "1. Confirm RU-A1 state and fronthaul once more against the approved recovery MOP.\n" +
        "2. Restart RU-A1 — the bridge already agreed this step but never executed it.\n" +
        "3. Verify service on Sector A and confirm the alarm clears before closing.",
    );
    parts.push(
      "```cta\nStart troubleshooting|Troubleshoot Radio Unit Communication Failure on SITE-102.\n```",
    );
  } else if (promptText.trim().length > 0) {
    parts.push(`${style.actionsHeading}:\n\n1. Review the items above and decide what needs an owner.`);
  }

  if (unavailable.length > 0) {
    parts.push(
      `${unavailable.length} attached source${unavailable.length === 1 ? "" : "s"} couldn't be read — the connector isn't connected.`,
    );
  }

  return parts.join("\n\n");
}
