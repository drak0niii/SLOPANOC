import type {
  AiModel,
  Citation,
  Connector,
  ScheduledTaskFrequency,
  Skill,
  TaskSource,
  UsageSummary,
} from "../types";
import { createId } from "../lib/id";
import { formatScheduleSummary } from "../lib/scheduledTasks";
import { SOURCE_KIND_BY_ID, formatSourceLabel } from "./workspaceSources";

export const MOCK_MODELS: AiModel[] = [
  { id: "model-gpt-5-6-sol", name: "GPT-5.6 Sol" },
  { id: "model-gpt-5-5", name: "GPT-5.5" },
  { id: "model-o3", name: "o3" },
];

export const DEFAULT_MODEL_ID = MOCK_MODELS[0].id;

export const MOCK_SKILLS: Skill[] = [
  {
    id: "skill-incident-investigator",
    name: "Incident Investigator",
    description: "Methodically root-causes incidents from available signals.",
    instructions:
      "Investigate the incident step by step. Ask for missing signals before concluding. Summarize root cause, impact, and next actions.",
  },
  {
    id: "skill-exec-comms",
    name: "Executive Communication",
    description: "Rewrites updates for a concise, leadership-ready tone.",
    instructions:
      "Respond with short, direct, outcome-first language suitable for an executive audience. Avoid jargon and hedging.",
  },
  {
    id: "skill-noc-troubleshooting",
    name: "NOC Troubleshooting",
    description: "Structured operational troubleshooting for network issues.",
    instructions:
      "Work through triage in a structured runbook style: symptoms, scope, likely cause, mitigation, escalation path.",
  },
  {
    id: "skill-concise-writing",
    name: "Concise Writing",
    description: "Trims responses to the shortest clear version.",
    instructions:
      "Answer as briefly as possible while remaining accurate and complete. Prefer short sentences and remove filler.",
  },
];

export const MOCK_CONNECTORS: Connector[] = [
  {
    id: "connector-sharepoint",
    name: "SharePoint",
    purpose: "Search and reference enterprise documents",
    state: "connected",
    capability: "read",
  },
  {
    id: "connector-outlook",
    name: "Outlook",
    purpose: "Email search and actions",
    state: "connected",
    capability: "read_write",
  },
  {
    // Connected by default: Teams backs the headline flows (handover source,
    // outage-bridge reading, distribution target). Shipping it disconnected
    // would block the primary demo on a setup step and make the first seeded
    // brief read as broken. Google Calendar and Gmail stay disconnected so
    // the unavailable path is still exercised.
    id: "connector-teams",
    name: "Microsoft Teams",
    purpose: "Search channels and send messages",
    state: "connected",
    capability: "read_write",
  },
  {
    id: "connector-google-calendar",
    name: "Google Calendar",
    purpose: "Manage your schedule and coordinate meetings effortlessly",
    state: "not_connected",
    capability: "read",
  },
  {
    id: "connector-gmail",
    name: "Gmail",
    purpose: "Draft replies, summarize threads, & search your inbox",
    state: "not_connected",
    capability: "read_write",
  },
  {
    id: "connector-microsoft-365",
    name: "Microsoft 365",
    purpose: "Access your company's SharePoint, OneDrive, Outlook, and Teams",
    state: "not_connected",
    capability: "read_write",
  },
];

/** Ids of every currently-connected connector, so a proposal only ever
 * suggests connectors it actually needs and doesn't already have. */
export function connectedConnectorIds(connectors: Record<string, Connector>): string[] {
  return Object.values(connectors)
    .filter((connector) => connector.state === "connected")
    .map((connector) => connector.id);
}

export const MOCK_USAGE: UsageSummary = {
  periodLabel: "Resets September 1",
  messagesUsed: 342,
  messagesLimit: 1000,
  modelBreakdown: [
    { label: "GPT-5.6 Sol", percent: 68 },
    { label: "GPT-5.5", percent: 24 },
    { label: "o3", percent: 8 },
  ],
  thinkingBreakdown: [
    { label: "Instant", percent: 76 },
    { label: "Advanced", percent: 24 },
  ],
  connectorBreakdown: [
    { label: "Outlook", actions: 42 },
    { label: "Microsoft Teams", actions: 18 },
    { label: "SharePoint", actions: 9 },
  ],
};

export const CONNECTOR_STATE_LABEL: Record<string, string> = {
  connected: "Connected",
  not_connected: "Not connected",
  permission_required: "Permission required",
  error: "Error",
};

// Connector used by the mock "send email" action-proposal scenario below.
export const ACTION_CONNECTOR_ID = "connector-outlook";

export interface MockResponseContext {
  /** Whether ACTION_CONNECTOR_ID is currently usable in the active workspace. */
  connectorAvailable: boolean;
  connectorName: string;
  /** Name of the active Project, or null when in the general workspace. */
  projectName: string | null;
  /** Live connector state, so a schedule proposal can suggest exactly the
   * connectors its inferred sources need and don't already have — the same
   * rule `skippedConnectorIds` applies on a real run. */
  connectedConnectorIds: string[];
  /** True once the chat's "schedule-setup" intro turn has played — every
   * reply after that is the user describing their scheduling request, so it
   * always gets treated as one rather than needing to say "schedule". */
  forceSchedule?: boolean;
}

interface MockActionProposalDraft {
  connectorId: string;
  actionType: string;
  title: string;
  fields: { label: string; value: string }[];
  body: string;
  scheduledTaskDraft?: {
    name: string;
    instructions: string;
    frequency: ScheduledTaskFrequency;
    timeOfDay: string;
    sources: TaskSource[];
  };
}

export type MockResponseResult =
  | { kind: "grounded"; text: string; citations: Citation[] }
  | { kind: "no_answer"; text: string; searchedScope: string[] }
  | { kind: "action_proposal"; text: string; proposal: MockActionProposalDraft }
  | { kind: "action_unavailable"; text: string; connectorId: string; connectorName: string }
  | {
      kind: "schedule_proposal";
      text: string;
      suggestedConnectorIds: string[];
      proposal: MockActionProposalDraft;
    };

/** Deliberately narrow — matches an actual request to set up a recurring
 * task ("schedule a task", "every weekday", "remind me daily"), not any
 * message that merely mentions a schedule or a one-off "remind me what…"
 * question. Broader substring matches like "schedul" or "remind me" used to
 * hijack ordinary questions (e.g. "what's on my schedule tomorrow?"). */
const SCHEDULE_INTENT_PATTERN =
  /\b(schedule|set up|create)\s+(a\s+)?(recurring\s+|scheduled\s+)?task\b|\brecurring task\b|\bevery\s+(day|morning|weekday|week|month)\b|\beach\s+(morning|day|week|month)\b|\bdaily at\b|\bremind me\s+(every|each|daily|weekly|monthly)\b|\bmorning brief(ing)?\b|\bdaily brief(ing)?\b/;

/** Very small, deliberately naive heuristics — this is a UI prototype, not a
 * real NLP scheduler. They just need to feel responsive to an obvious ask
 * like "every weekday at 8am" without pretending to understand more. */
function guessScheduleFrequency(lower: string): ScheduledTaskFrequency {
  if (lower.includes("weekday")) return "weekdays";
  if (lower.includes("week")) return "weekly";
  if (lower.includes("month")) return "monthly";
  if (lower.includes("day") || lower.includes("daily") || lower.includes("morning")) return "daily";
  return "manual";
}

/** Maps obvious phrases onto structured sources, so a described task arrives
 * pre-wired rather than needing every source added by hand afterwards. As
 * naive as its sibling heuristics — it only needs to feel responsive to an
 * explicit ask like "read my calendar and the Ops Bridge channel". */
function guessSources(lower: string): TaskSource[] {
  const sources: TaskSource[] = [];
  const add = (kind: TaskSource["kind"], scope: string) =>
    sources.push({ id: createId("src"), kind, scope });

  const mentionsHandover = /handover|hand-over|night shift/.test(lower);

  if (/calendar|meeting|schedule for|day ahead|diary/.test(lower)) {
    add("outlook_calendar", "Today");
  }
  if (/inbox|email|mail|folder/.test(lower)) {
    add("outlook_inbox", mentionsHandover ? "NOC Handover" : "Inbox");
  }
  if (/teams|channel|bridge|room|chat room/.test(lower) || mentionsHandover) {
    add("teams_channel", mentionsHandover ? "NOC Handover" : "Ops Bridge");
  }

  return sources;
}

/** Connectors the proposed sources genuinely need, minus the ones already
 * connected. Mirrors `skippedConnectorIds` in lib/taskRun.ts. */
function missingConnectorIds(sources: TaskSource[], connected: string[]): string[] {
  const needed = new Set(
    sources
      .map((source) => SOURCE_KIND_BY_ID[source.kind]?.connectorId)
      .filter((id): id is string => Boolean(id)),
  );
  return [...needed].filter((id) => !connected.includes(id));
}

/** The connector that would actually do the work. A scheduling proposal has
 * no single acting connector, so use the first source's, falling back to the
 * action connector when no sources were inferred. */
function primaryConnectorId(sources: TaskSource[]): string {
  const kind = sources[0] ? SOURCE_KIND_BY_ID[sources[0].kind] : undefined;
  return kind?.connectorId ?? ACTION_CONNECTOR_ID;
}

function guessScheduleTime(text: string): string {
  const match = text.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)/i);
  if (!match) return "09:00";
  let hour = Number(match[1]) % 12;
  if (match[3].toLowerCase() === "pm") hour += 12;
  const minute = match[2] ?? "00";
  return `${String(hour).padStart(2, "0")}:${minute}`;
}

function buildGroundedCitation(context: MockResponseContext): Citation {
  if (context.projectName) {
    return {
      id: "cite-project-alarm-handling",
      docId: "MOP-ALARM-002",
      docTitle: "Alarm Handling",
      version: "v2.1",
      status: "approved",
      section: "§4.3",
      excerpt:
        "Escalate to Tier 2 when signal loss persists beyond 5 minutes across more than one region.",
      scope: "project",
      scopeLabel: `${context.projectName} knowledge`,
    };
  }

  return {
    id: "cite-mop-core-014",
    docId: "MOP-CORE-014",
    docTitle: "Incident Response Baseline",
    version: "v2.1",
    status: "approved",
    section: "§4.3",
    excerpt:
      "Escalate to Tier 2 when signal loss persists beyond 5 minutes across more than one region.",
    scope: "global",
    scopeLabel: "Global approved baseline",
  };
}

/**
 * Centralized mock assistant response generation. All deterministic
 * prototype trigger conditions (action proposals, strict-grounding
 * "no answer" responses) live here so UI components stay free of
 * prompt-string logic.
 */
export function generateMockAssistantResponse(
  promptText: string,
  context: MockResponseContext,
): MockResponseResult {
  const trimmed = promptText.trim();
  const preview = trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed;
  const lower = trimmed.toLowerCase();

  if (context.forceSchedule || SCHEDULE_INTENT_PATTERN.test(lower)) {
    const frequency = guessScheduleFrequency(lower);
    const timeOfDay = guessScheduleTime(trimmed);
    const name = deriveChatTitle(trimmed);
    const sources = guessSources(lower);

    return {
      kind: "schedule_proposal",
      text: context.forceSchedule
        ? "Got it — here's what I'll set up."
        : "This matches a recurring task — let me set this up.",
      suggestedConnectorIds: missingConnectorIds(sources, context.connectedConnectorIds),
      proposal: {
        connectorId: primaryConnectorId(sources),
        actionType: "schedule_task",
        title: "Schedule task",
        fields: [
          { label: "Name", value: name },
          {
            label: "Frequency",
            value: formatScheduleSummary({ frequency, timeOfDay }),
          },
          {
            label: "Sources",
            value: sources.length > 0 ? sources.map(formatSourceLabel).join(", ") : "None yet",
          },
        ],
        body: trimmed,
        scheduledTaskDraft: { name, instructions: trimmed, frequency, timeOfDay, sources },
      },
    };
  }

  if (lower.includes("email")) {
    if (!context.connectorAvailable) {
      return {
        kind: "action_unavailable",
        text: `I can draft this, but sending it requires ${context.connectorName}, which isn't available in this workspace right now.`,
        connectorId: ACTION_CONNECTOR_ID,
        connectorName: context.connectorName,
      };
    }

    return {
      kind: "action_proposal",
      text: "Here's the email I'd send based on this conversation. Nothing is sent until you approve it.",
      proposal: {
        connectorId: ACTION_CONNECTOR_ID,
        actionType: "send_email",
        title: "Send email",
        fields: [
          { label: "To", value: "John Smith" },
          { label: "Subject", value: "Incident summary" },
        ],
        body: "Sharing a quick summary of the current incident status for visibility. Let me know if you need more detail.",
      },
    };
  }

  if (lower.includes("budget")) {
    const searchedScope = context.projectName
      ? ["Global approved baseline", `${context.projectName} knowledge`]
      : ["Global approved baseline"];

    return {
      kind: "no_answer",
      text: "The approved knowledge available to this workspace does not contain enough information to answer this.",
      searchedScope,
    };
  }

  return {
    kind: "grounded",
    text: `Here's a grounded summary based on the approved workspace knowledge for: "${preview}"\n\nBased on the available baseline, the recommended next step is to confirm scope before escalating. This is a mocked response for the UI prototype — no live retrieval or model call is happening yet.`,
    citations: [buildGroundedCitation(context)],
  };
}

export function deriveChatTitle(promptText: string): string {
  const trimmed = promptText.trim().replace(/\s+/g, " ");
  if (trimmed.length === 0) return "New chat";
  return trimmed.length > 48 ? `${trimmed.slice(0, 48)}…` : trimmed;
}
