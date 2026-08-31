import type { Citation, DemoScenarioId } from "../types";

/**
 * Deterministic executive-demo engine.
 *
 * All three scripted conversations dramatize the SAME RAN fault — Radio Unit
 * Communication Failure on SITE-102 / Sector A / RU-A1 — at three levels of
 * Copilot maturity (advise / refine / resolve). A chat enters a script only
 * when its opening prompt matches one of the triggers below; every following
 * user message in that chat simply advances to the next scripted assistant
 * turn (content-agnostic, fully deterministic — no live model, tool, or
 * network calls). Once a script is exhausted the chat falls back to the
 * normal generic mock response path.
 *
 * Message text uses a small fenced-block convention understood by
 * Message.tsx: ```check / ```action / ```output / ```finding / ```success /
 * ```automation / ```cta. Plain ``` fences and plain prose are unaffected.
 *
 * ```cta blocks are genuine quick-reply buttons ("Label|message to insert")
 * — clicking one inserts the message as a normal user turn via the same
 * sendMessage/demoRun path a manually typed reply would use.
 */

export interface DemoTurn {
  text: string;
  citations?: Citation[];
}

const MOP_CITATION: Citation = {
  id: "cite-mop-ran-ru-recovery-014",
  docId: "MOP-RAN-RU-RECOVERY-014",
  docTitle: "Radio Unit Communication Failure Recovery",
  version: "2.1",
  status: "approved",
  section: "Radio Unit recovery procedure",
  excerpt:
    "Validate fronthaul, hardware, and power state before restarting an unresponsive Radio Unit. Confirm no active change is in progress prior to recovery.",
  scope: "global",
  scopeLabel: "Global approved baseline",
};

/**
 * Matches a chat's opening prompt to a demo scenario. Requires the common
 * fault context plus a distinguishing verb so ordinary chats are never
 * hijacked by this deterministic script.
 */
export function matchDemoScenario(promptText: string): DemoScenarioId | null {
  const lower = promptText.toLowerCase();
  const isRuAlarmContext = lower.includes("radio unit communication failure") && lower.includes("site-102");
  if (!isRuAlarmContext) return null;

  if (lower.includes("resolve")) return "ru-resolve";
  if (lower.includes("investigate")) return "ru-refine";
  if (lower.includes("troubleshoot")) return "ru-advise";
  return null;
}

/* -------------------------------------------------------------------------- */
/* Phase 1 — ADVISE: engineer performs every check and the recovery itself.   */
/* -------------------------------------------------------------------------- */

const ADVISE_SCRIPT: DemoTurn[] = [
  {
    text:
      "Yes. I found the approved Radio Unit recovery MOP applicable to this alarm.\n\n" +
      "Let's first confirm the affected Radio Unit and whether the node can still communicate with it.\n\n" +
      "```check\nshow alarm detail SITE-102\n```\n\n" +
      "Connect to the node, run this command, and paste the output here.",
    citations: [MOP_CITATION],
  },
  {
    text:
      "Confirmed — the alarm is isolated to RU-A1 on Sector A.\n\n" +
      "Now let's check whether the Baseband still sees the Radio Unit and whether the fronthaul link is established.\n\n" +
      "```check\nshow radio-unit RU-A1 status\n```\n\n" +
      "Run it and paste the result.",
  },
  {
    text:
      "```finding\nThe fronthaul link is up, but RU-A1 itself is not responding.\n```\n\n" +
      "That makes a transport break unlikely. According to the MOP, the next step is to confirm there are no related hardware or power alarms before we restart anything.\n\n" +
      "```check\nshow active-alarms SITE-102 --radio RU-A1\n```\n\n" +
      "Please run this and paste the output.",
  },
  {
    text:
      "Good. There are no power, hardware, or fronthaul alarms explaining the failure.\n\n" +
      "```finding\nThe evidence now points to an unresponsive Radio Unit process rather than a physical link failure.\n```\n\n" +
      "Before recovery, let's confirm there is no active maintenance or configuration activity on the site.\n\n" +
      "```check\nshow active-changes SITE-102\n```\n\n" +
      "Paste the output when ready.",
  },
  {
    text:
      "No conflicting change is active.\n\n" +
      "Based on the approved MOP and the checks you've provided:\n\n" +
      "• Fronthaul is healthy\n" +
      "• Power and hardware alarms are absent\n" +
      "• RU-A1 is not responding\n" +
      "• No change activity is in progress\n\n" +
      "The recommended recovery step is to restart RU-A1.\n\n" +
      "```action\nrestart radio-unit RU-A1\n```\n\n" +
      "Run the restart and paste the result. Service on Sector A may briefly interrupt while the Radio Unit recovers.",
  },
  {
    text:
      "Great — RU-A1 completed the restart successfully.\n\n" +
      "Now let's verify that communication and service have recovered.\n\n" +
      "```check\nshow radio-unit RU-A1 status\n```",
  },
  {
    text:
      "```success\nRU-A1 is back online and communication is restored.\n```\n\n" +
      "Now one final check:\n\n" +
      "```check\nshow active-alarms SITE-102\n```\n\n" +
      "Run the final alarm check to confirm the incident is cleared.",
  },
  {
    text:
      "```success\nResolved.\n```\n\n" +
      "The Radio Unit Communication Failure alarm has cleared and Sector A is operational again.\n\n" +
      "Resolution: RU-A1 restart after validating fronthaul, hardware, power, and change state.",
  },
];

/* -------------------------------------------------------------------------- */
/* Phase 2 — REFINE: Copilot gathers the evidence itself after permission.    */
/* -------------------------------------------------------------------------- */

const REFINE_SCRIPT: DemoTurn[] = [
  {
    text:
      "Based on the approved recovery MOP, I would normally ask you to run these checks:\n\n" +
      "```check\nshow alarm detail SITE-102\nshow radio-unit RU-A1 status\nshow active-alarms SITE-102 --radio RU-A1\nshow active-changes SITE-102\n```\n\n" +
      "I can collect and evaluate this information directly instead of you running each command.\n\n" +
      "Would you like me to run the checks?\n\n" +
      "```cta\nRun checks|Yes, run them.\n```",
    citations: [MOP_CITATION],
  },
  {
    text:
      "Checking alarm context…\nChecking Radio Unit state…\nChecking related alarms…\nChecking active changes…\n\n" +
      "```finding\nRU-A1: Not responding\nFronthaul: Up\nPower alarms: None\nHardware alarms: None\nActive changes: None\n```\n\n" +
      "The fault is isolated to RU-A1.\n\n" +
      "The transport path is healthy and there is no hardware, power, or change condition preventing recovery.\n\n" +
      "The approved MOP recommends restarting the Radio Unit.\n\n" +
      "```automation\nThis troubleshooting sequence is repeatable and a good candidate for automation.\n\n" +
      "Would you like me to generate the MOP, send it for review and approval, and once approved place it into the Automate the Automation pipeline?\n```",
  },
];

/* -------------------------------------------------------------------------- */
/* Phase 3 — RESOLVE: approved bounded automation, explicit approval gate.    */
/* -------------------------------------------------------------------------- */

const RESOLVE_SCRIPT: DemoTurn[] = [
  {
    text:
      "This matches an approved bounded recovery scenario.\n\n" +
      "Approved scenario: CLS-RU-RECOVERY-014\n\n" +
      "I will:\n\n" +
      "1. Validate Radio Unit and fronthaul state\n" +
      "2. Confirm no conflicting change is active\n" +
      "3. Execute the approved RU-A1 recovery automation\n" +
      "4. Verify Radio Unit communication\n" +
      "5. Confirm the alarm has cleared\n\n" +
      "Would you like me to proceed?\n\n" +
      "```cta\nProceed|Proceed.\n```",
  },
  {
    text:
      "Validating conditions…\nExecuting approved recovery…\nVerifying service…\n\n" +
      "```success\nRecovery completed.\n```\n\n" +
      "RU-A1 communication is restored, Sector A is operational, and the Radio Unit Communication Failure alarm has cleared.\n\n" +
      "The execution has been recorded.",
  },
];

/* -------------------------------------------------------------------------- */
/* Scheduling Agent — a single scripted intro turn, entered directly from     */
/* "New task -> Create with SLOP ANOC" (see AppState's startScheduledTaskSetup */
/* and Sidebar/ScheduledTasksPage), not via matchDemoScenario. Whatever the   */
/* user says once this one turn is exhausted is treated as their scheduling  */
/* request (see generateMockAssistantResponse's `forceSchedule` context flag)*/
/* and turns into the same connector-suggestion + schedule-proposal flow a   */
/* normal chat gets when it mentions "schedule".                             */
/* -------------------------------------------------------------------------- */

export const SCHEDULE_SETUP_OPENING_PROMPT =
  "I want to set up a scheduled task. Briefly explain how scheduled tasks work, then ask me a few questions to figure out what I'd like you to do and when it should run.";

const SCHEDULE_SETUP_SCRIPT: DemoTurn[] = [
  {
    text:
      "Scheduled tasks let me run on a repeating cadence (or just once, manually) and act the same way I would in a regular chat — reading connected tools, drafting things, and delivering the result to you.\n\n" +
      "To set one up, tell me:\n\n" +
      "1. What would you like me to do each time it runs?\n" +
      "2. How often — daily, weekdays, weekly, monthly, or only when you trigger it manually?\n" +
      "3. Anywhere you'd like the result delivered, like a Teams chat room or an email inbox?",
  },
];

export const DEMO_SCRIPTS: Record<DemoScenarioId, DemoTurn[]> = {
  "ru-advise": ADVISE_SCRIPT,
  "ru-refine": REFINE_SCRIPT,
  "ru-resolve": RESOLVE_SCRIPT,
  "schedule-setup": SCHEDULE_SETUP_SCRIPT,
};
