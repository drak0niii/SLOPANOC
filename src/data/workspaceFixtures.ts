/**
 * Mock content behind every readable endpoint in `workspaceSources.ts`.
 *
 * Keyed by a source's `scope`, so `readSources()` can look content up without
 * any conditional logic per kind. Content deliberately shares a world with
 * `demoScript.ts` (SITE-102 / RU-A1 / MOP-RAN-RU-RECOVERY-014) so that an
 * outage-bridge summary can hand off into the existing troubleshooting script.
 *
 * Nothing here is fetched, generated, or randomised — a given task always
 * produces the same brief, which is what makes the prototype demoable.
 */

export interface CalendarEvent {
  id: string;
  timeLabel: string;
  title: string;
  attendees: string;
  note?: string;
}

export interface InboxItem {
  id: string;
  from: string;
  subject: string;
  preview: string;
  receivedLabel: string;
  urgent?: boolean;
}

export interface ChannelMessage {
  id: string;
  author: string;
  timeLabel: string;
  text: string;
}

export const CALENDAR_EVENTS: Record<string, CalendarEvent[]> = {
  Today: [
    {
      id: "cal-1",
      timeLabel: "08:30",
      title: "NOC handover sync",
      attendees: "Night shift + day shift",
      note: "Standing 15 min",
    },
    {
      id: "cal-2",
      timeLabel: "11:00",
      title: "CAB review — change CAB-4471",
      attendees: "Change board",
      note: "Covers SITE-102 firmware window",
    },
    {
      id: "cal-3",
      timeLabel: "15:00",
      title: "Vendor call — RU firmware roadmap",
      attendees: "Vendor TAM, RAN engineering",
    },
  ],
  Tomorrow: [
    {
      id: "cal-4",
      timeLabel: "09:00",
      title: "Post-incident review — INC-88214",
      attendees: "Incident commander, RAN, transport",
    },
    {
      id: "cal-5",
      timeLabel: "13:30",
      title: "Capacity planning workshop",
      attendees: "Planning team",
    },
  ],
  "This week": [
    {
      id: "cal-6",
      timeLabel: "Tue 11:00",
      title: "CAB review — change CAB-4471",
      attendees: "Change board",
    },
    {
      id: "cal-7",
      timeLabel: "Wed 09:00",
      title: "Post-incident review — INC-88214",
      attendees: "Incident commander, RAN, transport",
    },
    {
      id: "cal-8",
      timeLabel: "Fri 16:00",
      title: "Quarterly availability report walkthrough",
      attendees: "Service management",
    },
  ],
};

export const INBOX_ITEMS: Record<string, InboxItem[]> = {
  Inbox: [
    {
      id: "inb-1",
      from: "Service Desk",
      subject: "Weekly ticket volume summary",
      preview: "Volumes are flat week on week; two aged P3s need an owner.",
      receivedLabel: "07:12",
    },
    {
      id: "inb-2",
      from: "Vendor TAM",
      subject: "RU firmware 4.8.2 release notes",
      preview: "Includes the fronthaul keepalive fix you raised in March.",
      receivedLabel: "Yesterday",
    },
    {
      id: "inb-3",
      from: "Planning",
      subject: "Site access window confirmed",
      preview: "SITE-102 access approved for Thursday 02:00–05:00.",
      receivedLabel: "Yesterday",
    },
  ],
  "NOC Handover": [
    {
      id: "inb-4",
      from: "Night shift lead",
      subject: "Handover — INC-88214 escalated to Sev-2",
      preview:
        "RU-A1 on SITE-102 stopped responding at 02:14. Fronthaul is up, no power alarms. Escalated to Sev-2 at 04:40, awaiting day shift.",
      receivedLabel: "05:02",
      urgent: true,
    },
    {
      id: "inb-5",
      from: "Night shift lead",
      subject: "Handover — routine checks clear",
      preview: "All other sites nominal. Backup verification completed 03:15.",
      receivedLabel: "05:04",
    },
  ],
  Escalations: [
    {
      id: "inb-6",
      from: "Incident commander",
      subject: "INC-88214 — Sev-2 escalation, action required",
      preview: "Need a recovery plan and an ETA before the 11:00 CAB.",
      receivedLabel: "06:20",
      urgent: true,
    },
  ],
};

export const CHANNEL_MESSAGES: Record<string, ChannelMessage[]> = {
  /** Backs both the ad-hoc outage-bridge summary and a monitor-a-bridge source. */
  "Ops Bridge": [
    { id: "ch-1", author: "NOC operator", timeLabel: "02:14", text: "Alarm on SITE-102 — Sector A degraded, customers reporting no service." },
    { id: "ch-2", author: "NOC operator", timeLabel: "02:16", text: "Confirming scope — looks isolated to Sector A, other sectors nominal." },
    { id: "ch-3", author: "Transport", timeLabel: "02:22", text: "Fronthaul link to SITE-102 is up. No transport faults on our side." },
    { id: "ch-4", author: "RAN engineer", timeLabel: "02:31", text: "Baseband sees the cell but RU-A1 is not responding to management commands." },
    { id: "ch-5", author: "Power", timeLabel: "02:38", text: "No power alarms at the site. Rectifiers and battery all nominal." },
    { id: "ch-6", author: "Change management", timeLabel: "02:45", text: "No active change window on SITE-102 tonight. Nothing scheduled until CAB-4471." },
    { id: "ch-7", author: "Incident commander", timeLabel: "03:02", text: "So we have an unresponsive RU with healthy transport and power. Options?" },
    { id: "ch-8", author: "RAN engineer", timeLabel: "03:10", text: "Recovery MOP says validate then restart the radio unit. Need approval for the service hit." },
    { id: "ch-9", author: "Incident commander", timeLabel: "04:40", text: "Escalating to Sev-2 — approaching four hours. Handing to day shift with the restart pending." },
    { id: "ch-10", author: "Night shift lead", timeLabel: "05:00", text: "Day shift: RU-A1 restart is the agreed next step, not yet executed." },
  ],
  "NOC Handover": [
    { id: "ch-11", author: "Night shift lead", timeLabel: "04:55", text: "Handover: INC-88214 open, Sev-2, SITE-102 Sector A down since 02:14." },
    { id: "ch-12", author: "Night shift lead", timeLabel: "04:56", text: "Fronthaul verified up, no power or hardware alarms, no active changes." },
    { id: "ch-13", author: "Night shift lead", timeLabel: "04:57", text: "Agreed next step is an RU-A1 restart per MOP-RAN-RU-RECOVERY-014. Not executed." },
    { id: "ch-14", author: "Night shift lead", timeLabel: "04:58", text: "Everything else nominal. No other open incidents." },
  ],
  "Change Management": [
    { id: "ch-15", author: "Change board", timeLabel: "Yesterday 16:40", text: "CAB-4471 raised — SITE-102 RU firmware upgrade, window Thursday 02:00." },
    { id: "ch-16", author: "Change board", timeLabel: "Yesterday 16:52", text: "Pending approval, review at the 11:00 CAB." },
  ],
};

export const SHAREPOINT_DOCS: Record<string, { id: string; title: string; note: string }[]> = {
  Runbooks: [
    { id: "sp-1", title: "MOP-RAN-RU-RECOVERY-014", note: "Radio Unit Communication Failure Recovery, v2.1 (approved)" },
    { id: "sp-2", title: "MOP-ALARM-002", note: "Alarm handling and escalation thresholds, v2.1" },
  ],
  "Post-incident reviews": [
    { id: "sp-3", title: "PIR — INC-87990", note: "Previous RU failure at SITE-114, resolved by restart" },
  ],
};

/**
 * How an ad-hoc source summary is framed, keyed by the chat's active skill.
 * Lets the same reads read differently under a skill without any NLP.
 */
export const BRIEF_STYLE: Record<string, { lead: string; actionsHeading: string }> = {
  "skill-incident-investigator": {
    lead: "Working through this as an incident.",
    actionsHeading: "Investigation steps",
  },
  "skill-noc-troubleshooting": {
    lead: "Triaging this in runbook order.",
    actionsHeading: "Triage steps",
  },
  "skill-exec-comms": {
    lead: "Here is the leadership-ready version.",
    actionsHeading: "Recommended decisions",
  },
  "skill-concise-writing": {
    lead: "Short version.",
    actionsHeading: "Next steps",
  },
};

export const DEFAULT_BRIEF_STYLE = {
  lead: "Here's what's happening.",
  actionsHeading: "Suggested next actions",
};

/**
 * How a scheduled run is framed, keyed by a tone derived from the task's own
 * instructions. Same idea as BRIEF_STYLE above — that one keys off the chat's
 * active skill, this one off what the user asked the task to do.
 *
 * Urgency and brevity are two orthogonal axes rather than one exclusive tone:
 * the shipped "Daily briefing" template asks for both ("anything that needs my
 * attention" AND "keep it short"), and an exclusive tone would silently drop
 * one of the user's two explicit asks.
 */
export type RunToneId = "digest" | "urgent" | "short" | "urgent-short";

export const RUN_STYLE: Record<
  RunToneId,
  { lead: string; closing: string; urgencyFirst: boolean; headlinesOnly: boolean }
> = {
  digest: {
    lead: "Here's what came in.",
    closing: "What should I prioritise from this?",
    urgencyFirst: false,
    headlinesOnly: false,
  },
  urgent: {
    lead: "Anything that needs you is at the top.",
    closing: "What should I do about the item at the top?",
    urgencyFirst: true,
    headlinesOnly: false,
  },
  short: {
    lead: "Short version.",
    closing: "Expand any of these?",
    urgencyFirst: false,
    headlinesOnly: true,
  },
  "urgent-short": {
    lead: "Short version, with anything urgent first.",
    closing: "What should I do about the item at the top?",
    urgencyFirst: true,
    headlinesOnly: true,
  },
};

export const DEFAULT_RUN_STYLE = RUN_STYLE.digest;
