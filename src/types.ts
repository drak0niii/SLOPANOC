/** Stops on one axis, ordered from fastest to most thorough. The slider
 * derives its geometry from this order, so adding a stop is a one-line change
 * here plus a label. */
export type ThinkingEffort = "instant" | "advanced";

export interface Skill {
  id: string;
  name: string;
  description: string;
  instructions: string;
}

export interface AiModel {
  id: string;
  name: string;
}

export type ConnectorState =
  | "connected"
  | "not_connected"
  | "permission_required"
  | "error";

export interface Connector {
  id: string;
  name: string;
  purpose: string;
  state: ConnectorState;
  capability: "read" | "read_write";
}

export type AttachmentKind = "file" | "folder";

export interface Attachment {
  id: string;
  kind: AttachmentKind;
  name: string;
  meta?: string;
  /** Marks the mock "Pasted text.txt" attachment created when a message's
   * typed/pasted text exceeds the long-paste threshold — lets the message
   * renderer hide the raw inline text and offer "Expand message" instead. */
  isPastedText?: boolean;
  /** The full pasted text, present only on `isPastedText` attachments —
   * carries the content from the moment of paste (before it's ever in
   * `draft.text`) through to the sent message. */
  content?: string;
}

export type CitationScope = "global" | "project";

export interface Citation {
  id: string;
  docId: string;
  docTitle: string;
  version: string;
  status: "approved";
  section: string;
  excerpt: string;
  scope: CitationScope;
  scopeLabel: string;
}

export type MessageStatus = "pending" | "complete";

export type GroundingResult = "insufficient";

export interface ActionProposalField {
  label: string;
  value: string;
}

export type ActionProposalStatus =
  | "pending"
  | "approved"
  | "processing"
  | "cancelled"
  | "completed";

export interface ActionProposal {
  id: string;
  chatId: string;
  connectorId: string;
  actionType: string;
  title: string;
  fields: ActionProposalField[];
  body: string;
  status: ActionProposalStatus;
  /** Present only for `actionType === "schedule_task"` — the data actually
   * used to create the ScheduledTask once the proposal is approved. */
  scheduledTaskDraft?: {
    name: string;
    instructions: string;
    frequency: ScheduledTaskFrequency;
    timeOfDay: string;
    sources: TaskSource[];
  };
  /** Set once the proposal is approved and a real ScheduledTask is created —
   * lets the UI link straight to it instead of guessing by name/instructions. */
  createdTaskId?: string;
}

export type ConnectorUnavailableReason = "global_unavailable" | "project_disabled";

export interface ConnectorUnavailableInfo {
  connectorId: string;
  connectorName: string;
  reason: ConnectorUnavailableReason;
}

export interface Message {
  id: string;
  chatId: string;
  role: "user" | "assistant";
  text: string;
  status: MessageStatus;
  citations?: Citation[];
  attachments?: Attachment[];
  groundingResult?: GroundingResult;
  searchedScope?: string[];
  actionProposalId?: string;
  connectorUnavailable?: ConnectorUnavailableInfo;
  /** Connectors surfaced as "could help with this" — shown as a mock
   * connect-prompt card above any action proposal on the same message. */
  suggestedConnectorIds?: string[];
  /** A "send this response to…" proposal raised from the message action row.
   * Kept separate from `actionProposalId` so sharing never clobbers a
   * proposal the assistant already attached to the same message. */
  distributionProposalId?: string;
  /** Ad-hoc chat-room / calendar / inbox endpoints attached to a user
   * message via the composer's "+" menu. */
  sources?: TaskSource[];
  createdAt: number;
  /** Optional speaker name shown above the message. Not used by current mock content. */
  speakerLabel?: string;
}

/** Identifies which deterministic executive-demo script a chat is following. */
export type DemoScenarioId = "ru-advise" | "ru-refine" | "ru-resolve" | "schedule-setup";

/**
 * Runtime progress through a demo script. Set only when a chat's opening
 * prompt matches one of the deterministic demo triggers — never seeded.
 */
export interface DemoRun {
  scenarioId: DemoScenarioId;
  /** Index of the next scripted assistant turn to play. */
  step: number;
}

export interface Chat {
  id: string;
  title: string;
  projectId: string | null;
  pinned: boolean;
  pinnedAt?: number;
  /** Mock "unread" flag toggled from the chat's menu — cleared automatically
   * when the chat is opened, same as any normal inbox. */
  unread?: boolean;
  createdAt: number;
  messageIds: string[];
  activeSkillId: string | null;
  connectorIds: string[];
  selectedModelId: string;
  thinkingEffort: ThinkingEffort;
  /** Present only while this chat is following a deterministic demo script. */
  demoRun?: DemoRun;
  /** Set when this chat is owned by a scheduled task. Owned chats are listed
   * under Tasks in the sidebar, never under Chats — one task, one row. */
  scheduledTaskId?: string;
}

export interface Project {
  id: string;
  name: string;
  /** Short "what this project is for" blurb, shown on the projects grid.
   * Distinct from `instructionsText`, which steers the assistant. */
  description?: string;
  instructionsText: string;
  connectorIds: string[];
  fileIds: string[];
  createdAt: number;
  pinned?: boolean;
  pinnedAt?: number;
}

export interface ProjectFile {
  id: string;
  name: string;
  size: string;
  type: string;
  selectedAt: number;
  /** Text content shown in the file preview dialog — read from the real
   * file for text-like types, otherwise a labeled mock placeholder. */
  content: string;
}

export type ScheduledTaskFrequency = "manual" | "daily" | "weekdays" | "weekly" | "monthly";
export type ScheduledTaskPermission = "manual_approve" | "auto_run";

/**
 * A typed endpoint inside a connector. The same shape backs all three
 * workspace features: a scheduled task reads several on a cadence, a chat
 * reads one ad hoc, and a distribution writes to one.
 */
export type SourceKindId =
  | "outlook_calendar"
  | "outlook_inbox"
  | "teams_channel"
  | "sharepoint_folder";

export interface TaskSource {
  id: string;
  kind: SourceKindId;
  /** Which slice of the endpoint: a range ("Today"), a folder ("NOC
   * Handover"), or a channel ("Ops Bridge"). Always one of the kind's
   * `scopeOptions`, so a run is guaranteed to find fixture content. */
  scope: string;
}

export type DestinationKindId = "teams_channel" | "email";

/** Where a result gets posted. Replaces the old free-text
 * teamsChatRoom/inboxEmail pair. */
export interface Distribution {
  kind: DestinationKindId;
  /** Channel name or email address. */
  target: string;
}

/** One execution of a scheduled task. */
export interface TaskRun {
  id: string;
  startedAt: number;
  status: "running" | "completed";
  /** The assistant message in the task's chat carrying this run's output. */
  messageId: string;
  summary?: string;
  /** Connectors that were unavailable at run time — explains a thin brief. */
  skippedConnectorIds?: string[];
  trigger: "manual" | "seed";
}

/** A mocked recurring task. There is no real scheduler: runs happen only via
 * "Run now" and the single backdated run seeded at creation. */
export interface ScheduledTask {
  id: string;
  /** Optional — a scheduled task can stand alone, not just live inside a
   * project (matches the reference product's global "Scheduled tasks" list). */
  projectId?: string;
  name: string;
  instructions: string;
  modelId: string;
  frequency: ScheduledTaskFrequency;
  /** 24h "HH:mm", relevant for every frequency except "manual". */
  timeOfDay: string;
  permission: ScheduledTaskPermission;
  /** Structured, connector-backed inputs each run reads from. */
  sources: TaskSource[];
  /** Where results are offered for posting. Never auto-sent — each run ends
   * in a pending ActionProposal, per the explicit-confirmation rule. */
  distributions: Distribution[];
  /** The one chat this task owns. Runs append to it and the user can ask
   * follow-ups there. Undefined until the first run creates it. */
  chatId?: string;
  /** Oldest first. */
  runs: TaskRun[];
  active: boolean;
  createdAt: number;
}

export type WorkspaceScope = { type: "general" } | { type: "project"; projectId: string };

export type SettingsSection = "usage" | "connectors" | "skills";

export type ProjectSettingsSection = "name" | "instructions" | "connectors" | "files";

export interface UsageBreakdownItem {
  label: string;
  percent: number;
}

export interface ConnectorUsageItem {
  label: string;
  actions: number;
}

export interface UsageSummary {
  periodLabel: string;
  messagesUsed: number;
  messagesLimit: number;
  modelBreakdown: UsageBreakdownItem[];
  thinkingBreakdown: UsageBreakdownItem[];
  connectorBreakdown: ConnectorUsageItem[];
}
