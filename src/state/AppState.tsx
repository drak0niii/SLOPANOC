import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type ReactNode,
} from "react";
import type {
  ActionProposal,
  ActionProposalStatus,
  Attachment,
  Chat,
  Connector,
  ConnectorUnavailableReason,
  DemoRun,
  Message,
  Project,
  ProjectFile,
  ProjectSettingsSection,
  ScheduledTask,
  SettingsSection,
  Skill,
  TaskRun,
  TaskSource,
  ThinkingEffort,
  WorkspaceScope,
} from "../types";
import { createId } from "../lib/id";
import { LONG_PASTE_THRESHOLD } from "../lib/constants";
import {
  ACTION_CONNECTOR_ID,
  DEFAULT_MODEL_ID,
  connectedConnectorIds,
  MOCK_CONNECTORS,
  MOCK_SKILLS,
  deriveChatTitle,
  generateMockAssistantResponse,
  type MockResponseResult,
} from "../data/mock";
import { DEMO_SCRIPTS, SCHEDULE_SETUP_OPENING_PROMPT, matchDemoScenario } from "../data/demoScript";
import {
  composeRunMessage,
  composeSourceBrief,
  formatRunLabel,
  readSources,
  skippedConnectorIds,
} from "../lib/taskRun";
import { previousOccurrence } from "../lib/scheduledTasks";
import { previewForDistribution } from "../lib/distribution";
import { findDestination, findDestinationByValue } from "../data/workspaceSources";

/** Shared by `sendMessage` and `regenerateMessage` — both end with the same
 * "turn a mock response into a COMPLETE_ASSISTANT_MESSAGE dispatch" step. */
function dispatchMockResponse(
  dispatch: React.Dispatch<Action>,
  assistantMessageId: string,
  response: MockResponseResult,
  connector: Connector | undefined,
) {
  if (response.kind === "grounded") {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: { messageId: assistantMessageId, text: response.text, citations: response.citations },
    });
  } else if (response.kind === "no_answer") {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        groundingResult: "insufficient",
        searchedScope: response.searchedScope,
      },
    });
  } else if (response.kind === "action_unavailable") {
    const reason: ConnectorUnavailableReason =
      connector?.state !== "connected" ? "global_unavailable" : "project_disabled";
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        connectorUnavailable: {
          connectorId: response.connectorId,
          connectorName: response.connectorName,
          reason,
        },
      },
    });
  } else {
    dispatch({
      type: "COMPLETE_ASSISTANT_MESSAGE",
      payload: {
        messageId: assistantMessageId,
        text: response.text,
        // Normalised to undefined when empty, matching runScheduledTask — an
        // empty array would still satisfy the render guard downstream.
        suggestedConnectorIds:
          response.kind === "schedule_proposal" && response.suggestedConnectorIds.length > 0
            ? response.suggestedConnectorIds
            : undefined,
        actionProposal: { id: createId("action"), ...response.proposal },
      },
    });
  }
}

const DEFAULT_THINKING_EFFORT: ThinkingEffort = "instant";

interface DraftState {
  text: string;
  attachments: Attachment[];
  /** Chat rooms / calendars / folders attached via the composer's "+" menu,
   * read once when the message is sent. */
  sources: TaskSource[];
  thinkingEffort: ThinkingEffort;
  skillId: string | null;
  connectorIds: string[];
  modelId: string;
}

function freshDraft(): DraftState {
  return {
    text: "",
    attachments: [],
    sources: [],
    thinkingEffort: DEFAULT_THINKING_EFFORT,
    skillId: null,
    connectorIds: [],
    modelId: DEFAULT_MODEL_ID,
  };
}

interface AppState {
  chats: Record<string, Chat>;
  chatOrder: string[];
  messages: Record<string, Message>;
  projects: Record<string, Project>;
  projectOrder: string[];
  projectFiles: Record<string, ProjectFile>;
  scheduledTasks: Record<string, ScheduledTask>;
  connectors: Record<string, Connector>;
  skills: Record<string, Skill>;
  skillOrder: string[];
  actionProposals: Record<string, ActionProposal>;
  settingsModal: { open: boolean; section: SettingsSection };
  projectSettingsModal: { open: boolean; section: ProjectSettingsSection };
  workspaceScope: WorkspaceScope;
  activeChatId: string | null;
  sidebarCollapsed: boolean;
  /** Full-page browsers shown instead of the normal workspace/project-home
   * content — mutually exclusive with each other and with a normal chat. */
  mainView: "workspace" | "allProjects" | "scheduledTasks" | "scheduledTaskDetail";
  /** Which task "scheduledTaskDetail" is currently showing. */
  activeScheduledTaskId: string | null;
  /** Timestamp of the last "you're already here" composer nudge — bumped
   * when New chat is clicked while already on a fresh, empty chat. 0 means
   * never nudged yet, so PromptComposer's effect doesn't fire on mount. */
  composerNudgeAt: number;
  draft: DraftState;
}

const initialState: AppState = {
  chats: {},
  chatOrder: [],
  messages: {},
  projects: {},
  projectOrder: [],
  projectFiles: {},
  scheduledTasks: {},
  connectors: Object.fromEntries(MOCK_CONNECTORS.map((c) => [c.id, c])),
  skills: Object.fromEntries(MOCK_SKILLS.map((s) => [s.id, s])),
  skillOrder: MOCK_SKILLS.map((s) => s.id),
  actionProposals: {},
  settingsModal: { open: false, section: "usage" },
  projectSettingsModal: { open: false, section: "name" },
  workspaceScope: { type: "general" },
  activeChatId: null,
  sidebarCollapsed: false,
  mainView: "workspace",
  activeScheduledTaskId: null,
  composerNudgeAt: 0,
  draft: freshDraft(),
};

type Action =
  | {
      type: "SEND_MESSAGE";
      payload: {
        chatId: string;
        isNewChat: boolean;
        userMessageId: string;
        assistantMessageId: string;
        text: string;
        attachments: Attachment[];
        sources?: TaskSource[];
        timestamp: number;
        demoRun?: DemoRun;
      };
    }
  | {
      /** Always starts a brand-new, standalone chat running the
       * "schedule-setup" script — unlike SEND_MESSAGE, never reuses
       * whatever chat/project happened to be active beforehand. */
      type: "START_SCHEDULING_CHAT";
      payload: {
        chatId: string;
        userMessageId: string;
        assistantMessageId: string;
        text: string;
        timestamp: number;
      };
    }
  | {
      type: "COMPLETE_ASSISTANT_MESSAGE";
      payload: {
        messageId: string;
        text: string;
        citations?: Message["citations"];
        groundingResult?: Message["groundingResult"];
        searchedScope?: string[];
        connectorUnavailable?: Message["connectorUnavailable"];
        suggestedConnectorIds?: string[];
        actionProposal?: {
          id: string;
          connectorId: string;
          actionType: string;
          title: string;
          fields: ActionProposal["fields"];
          body: string;
          scheduledTaskDraft?: ActionProposal["scheduledTaskDraft"];
        };
        /** When present, advances the owning chat's demo script to this step. */
        demoStep?: number;
      };
    }
  | { type: "REGENERATE_MESSAGE"; payload: { messageId: string } }
  | {
      type: "EDIT_MESSAGE";
      payload: {
        chatId: string;
        messageId: string;
        text: string;
        assistantMessageId: string;
        /** True when editing the chat's first message — re-derives the
         * chat title and drops any demo script, same as a fresh opening. */
        retitle: boolean;
      };
    }
  | { type: "NEW_CHAT" }
  | { type: "NUDGE_COMPOSER"; payload: { timestamp: number } }
  | { type: "SELECT_CHAT"; payload: { chatId: string } }
  | { type: "SET_DRAFT_TEXT"; payload: { text: string } }
  | { type: "SET_THINKING_EFFORT"; payload: { value: ThinkingEffort } }
  | { type: "SET_SKILL"; payload: { skillId: string | null } }
  | { type: "SET_MODEL"; payload: { modelId: string } }
  | { type: "TOGGLE_PIN"; payload: { chatId: string; timestamp: number } }
  | { type: "TOGGLE_UNREAD"; payload: { chatId: string } }
  | { type: "RENAME_CHAT"; payload: { chatId: string; title: string } }
  | { type: "DELETE_CHAT"; payload: { chatId: string } }
  | { type: "TOGGLE_CONNECTOR"; payload: { connectorId: string } }
  | { type: "ADD_ATTACHMENTS"; payload: { attachments: Attachment[] } }
  | { type: "REMOVE_ATTACHMENT"; payload: { id: string } }
  | { type: "TOGGLE_SIDEBAR" }
  | {
      type: "CREATE_PROJECT";
      payload: { projectId: string; name: string; description?: string; timestamp: number };
    }
  | { type: "ENTER_PROJECT"; payload: { projectId: string } }
  | { type: "SET_PROJECT_INSTRUCTIONS"; payload: { projectId: string; text: string } }
  | {
      type: "RENAME_PROJECT";
      payload: { projectId: string; name: string; description?: string };
    }
  | { type: "DELETE_PROJECT"; payload: { projectId: string } }
  | { type: "TOGGLE_PROJECT_PINNED"; payload: { projectId: string; timestamp: number } }
  | { type: "TOGGLE_PROJECT_CONNECTOR"; payload: { projectId: string; connectorId: string } }
  | { type: "ADD_PROJECT_FILES"; payload: { projectId: string; files: ProjectFile[] } }
  | { type: "REMOVE_PROJECT_FILE"; payload: { projectId: string; fileId: string } }
  | {
      /** Begins a run. The owned chat is resolved *inside the reducer* from
       * live state — see the case body for why the pre-minted chatId can't be
       * trusted. */
      type: "START_TASK_RUN";
      payload: {
        taskId: string;
        runId: string;
        chatId: string;
        messageId: string;
        speakerLabel: string;
        timestamp: number;
        focus: boolean;
      };
    }
  | {
      type: "COMPLETE_TASK_RUN";
      payload: { taskId: string; runId: string; summary: string; skippedConnectorIds?: string[] };
    }
  | {
      /** Inserts an already-complete, backdated run so a new task has
       * something to show without pretending a live run just happened. */
      type: "SEED_TASK_RUN";
      payload: {
        taskId: string;
        runId: string;
        chatId: string;
        messageId: string;
        text: string;
        summary: string;
        speakerLabel: string;
        ranAt: number;
        suggestedConnectorIds?: string[];
      };
    }
  | {
      type: "PROPOSE_DISTRIBUTION";
      payload: {
        proposalId: string;
        messageId: string;
        connectorId: string;
        destinationLabel: string;
        body: string;
      };
    }
  | { type: "ADD_DRAFT_SOURCE"; payload: { source: TaskSource } }
  | { type: "REMOVE_DRAFT_SOURCE"; payload: { id: string } }
  | { type: "CREATE_SCHEDULED_TASK"; payload: ScheduledTask }
  | { type: "UPDATE_SCHEDULED_TASK"; payload: ScheduledTask }
  | { type: "DELETE_SCHEDULED_TASK"; payload: { id: string } }
  | { type: "TOGGLE_SCHEDULED_TASK_ACTIVE"; payload: { id: string } }
  | { type: "OPEN_ALL_PROJECTS" }
  | { type: "CLOSE_ALL_PROJECTS" }
  | { type: "OPEN_SCHEDULED_TASKS" }
  | { type: "VIEW_SCHEDULED_TASK"; payload: { id: string } }
  | {
      type: "MOVE_CHAT_TO_PROJECT";
      payload: { chatId: string; projectId: string | null; timestamp: number };
    }
  | { type: "CONNECT_CONNECTOR"; payload: { connectorId: string } }
  | { type: "DISCONNECT_CONNECTOR"; payload: { connectorId: string } }
  | {
      type: "CREATE_SKILL";
      payload: { skillId: string; name: string; description: string; instructions: string };
    }
  | {
      type: "UPDATE_SKILL";
      payload: { skillId: string; name: string; description: string; instructions: string };
    }
  | { type: "DELETE_SKILL"; payload: { skillId: string } }
  | {
      type: "SET_ACTION_PROPOSAL_STATUS";
      payload: { id: string; status: ActionProposalStatus; createdTaskId?: string };
    }
  | { type: "OPEN_SETTINGS"; payload: { section?: SettingsSection } }
  | { type: "CLOSE_SETTINGS" }
  | { type: "SET_SETTINGS_SECTION"; payload: { section: SettingsSection } }
  | { type: "OPEN_PROJECT_SETTINGS"; payload: { section?: ProjectSettingsSection } }
  | { type: "CLOSE_PROJECT_SETTINGS" }
  | { type: "SET_PROJECT_SETTINGS_SECTION"; payload: { section: ProjectSettingsSection } };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SEND_MESSAGE": {
      const {
        chatId, isNewChat, userMessageId, assistantMessageId, text, attachments, sources, timestamp, demoRun,
      } = action.payload;

      const chat: Chat = isNewChat
        ? {
            id: chatId,
            title: deriveChatTitle(text),
            projectId: state.workspaceScope.type === "project" ? state.workspaceScope.projectId : null,
            pinned: false,
            createdAt: timestamp,
            messageIds: [],
            activeSkillId: state.draft.skillId,
            connectorIds: state.draft.connectorIds,
            selectedModelId: state.draft.modelId,
            thinkingEffort: state.draft.thinkingEffort,
            demoRun,
          }
        : state.chats[chatId];

      const userMessage: Message = {
        id: userMessageId,
        chatId,
        role: "user",
        text,
        status: "complete",
        attachments,
        sources: sources && sources.length > 0 ? sources : undefined,
        createdAt: timestamp,
      };
      const assistantMessage: Message = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: timestamp + 1,
      };

      const updatedChat: Chat = {
        ...chat,
        messageIds: [...chat.messageIds, userMessageId, assistantMessageId],
      };

      return {
        ...state,
        chats: { ...state.chats, [chatId]: updatedChat },
        chatOrder: isNewChat ? [chatId, ...state.chatOrder] : state.chatOrder,
        messages: {
          ...state.messages,
          [userMessageId]: userMessage,
          [assistantMessageId]: assistantMessage,
        },
        activeChatId: chatId,
        draft: { ...state.draft, text: "", attachments: [], sources: [] },
      };
    }

    case "START_SCHEDULING_CHAT": {
      const { chatId, userMessageId, assistantMessageId, text, timestamp } = action.payload;

      const chat: Chat = {
        id: chatId,
        title: "New scheduled task",
        projectId: null,
        pinned: false,
        createdAt: timestamp,
        messageIds: [userMessageId, assistantMessageId],
        activeSkillId: null,
        connectorIds: [],
        selectedModelId: DEFAULT_MODEL_ID,
        thinkingEffort: DEFAULT_THINKING_EFFORT,
        demoRun: { scenarioId: "schedule-setup", step: 0 },
      };
      const userMessage: Message = {
        id: userMessageId,
        chatId,
        role: "user",
        text,
        status: "complete",
        createdAt: timestamp,
      };
      const assistantMessage: Message = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: timestamp + 1,
      };

      return {
        ...state,
        chats: { ...state.chats, [chatId]: chat },
        chatOrder: [chatId, ...state.chatOrder],
        messages: { ...state.messages, [userMessageId]: userMessage, [assistantMessageId]: assistantMessage },
        activeChatId: chatId,
        workspaceScope: { type: "general" },
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "COMPLETE_ASSISTANT_MESSAGE": {
      const {
        messageId,
        text,
        citations,
        groundingResult,
        searchedScope,
        connectorUnavailable,
        suggestedConnectorIds,
        actionProposal,
        demoStep,
      } = action.payload;
      const existing = state.messages[messageId];
      if (!existing) return state;

      const updatedMessage: Message = {
        ...existing,
        text,
        citations,
        groundingResult,
        searchedScope,
        connectorUnavailable,
        suggestedConnectorIds,
        actionProposalId: undefined,
        status: "complete",
      };

      let nextActionProposals = state.actionProposals;
      if (actionProposal) {
        updatedMessage.actionProposalId = actionProposal.id;
        nextActionProposals = {
          ...state.actionProposals,
          [actionProposal.id]: {
            id: actionProposal.id,
            chatId: existing.chatId,
            connectorId: actionProposal.connectorId,
            actionType: actionProposal.actionType,
            title: actionProposal.title,
            fields: actionProposal.fields,
            body: actionProposal.body,
            scheduledTaskDraft: actionProposal.scheduledTaskDraft,
            status: "pending",
          },
        };
      }

      let nextChats = state.chats;
      if (demoStep !== undefined) {
        const chat = state.chats[existing.chatId];
        if (chat?.demoRun) {
          nextChats = {
            ...state.chats,
            [chat.id]: { ...chat, demoRun: { ...chat.demoRun, step: demoStep } },
          };
        }
      }

      return {
        ...state,
        messages: { ...state.messages, [messageId]: updatedMessage },
        actionProposals: nextActionProposals,
        chats: nextChats,
      };
    }

    case "REGENERATE_MESSAGE": {
      const existing = state.messages[action.payload.messageId];
      if (!existing || existing.role !== "assistant") return state;
      return {
        ...state,
        messages: {
          ...state.messages,
          [existing.id]: {
            ...existing,
            text: "",
            status: "pending",
            citations: undefined,
            groundingResult: undefined,
            searchedScope: undefined,
            connectorUnavailable: undefined,
            actionProposalId: undefined,
          },
        },
      };
    }

    case "EDIT_MESSAGE": {
      const { chatId, messageId, text, assistantMessageId, retitle } = action.payload;
      const chat = state.chats[chatId];
      const message = state.messages[messageId];
      if (!chat || !message) return state;

      const editIndex = chat.messageIds.indexOf(messageId);
      if (editIndex === -1) return state;

      // Editing a message discards it and everything that followed — the
      // conversation continues fresh from the edited text.
      const keptIds = chat.messageIds.slice(0, editIndex);
      const removedIds = chat.messageIds.slice(editIndex);

      const remainingMessages = { ...state.messages };
      const remainingActionProposals = { ...state.actionProposals };
      for (const id of removedIds) {
        const removed = remainingMessages[id];
        if (removed?.actionProposalId) delete remainingActionProposals[removed.actionProposalId];
        delete remainingMessages[id];
      }

      remainingMessages[messageId] = { ...message, text };
      remainingMessages[assistantMessageId] = {
        id: assistantMessageId,
        chatId,
        role: "assistant",
        text: "",
        status: "pending",
        createdAt: Date.now(),
      };

      return {
        ...state,
        messages: remainingMessages,
        actionProposals: remainingActionProposals,
        chats: {
          ...state.chats,
          [chatId]: {
            ...chat,
            title: retitle ? deriveChatTitle(text) : chat.title,
            demoRun: retitle ? undefined : chat.demoRun,
            messageIds: [...keptIds, messageId, assistantMessageId],
          },
        },
      };
    }

    case "NEW_CHAT":
      return {
        ...state,
        activeChatId: null,
        workspaceScope: { type: "general" },
        mainView: "workspace",
        draft: freshDraft(),
      };

    case "NUDGE_COMPOSER":
      return { ...state, composerNudgeAt: action.payload.timestamp };

    case "SELECT_CHAT": {
      const chat = state.chats[action.payload.chatId];
      const nextScope: WorkspaceScope = chat?.projectId
        ? { type: "project", projectId: chat.projectId }
        : { type: "general" };
      return {
        ...state,
        activeChatId: action.payload.chatId,
        workspaceScope: nextScope,
        mainView: "workspace",
        // Opening a chat marks it read, same as any normal inbox.
        chats: chat?.unread
          ? { ...state.chats, [chat.id]: { ...chat, unread: false } }
          : state.chats,
        draft: { ...state.draft, text: "", attachments: [] },
      };
    }

    case "SET_DRAFT_TEXT":
      return { ...state, draft: { ...state.draft, text: action.payload.text } };

    case "SET_THINKING_EFFORT": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, thinkingEffort: action.payload.value },
          },
        };
      }
      return { ...state, draft: { ...state.draft, thinkingEffort: action.payload.value } };
    }

    case "SET_SKILL": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, activeSkillId: action.payload.skillId },
          },
        };
      }
      return { ...state, draft: { ...state.draft, skillId: action.payload.skillId } };
    }

    case "SET_MODEL": {
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        return {
          ...state,
          chats: {
            ...state.chats,
            [chat.id]: { ...chat, selectedModelId: action.payload.modelId },
          },
        };
      }
      return { ...state, draft: { ...state.draft, modelId: action.payload.modelId } };
    }

    case "TOGGLE_PIN": {
      const chat = state.chats[action.payload.chatId];
      if (!chat) return state;
      const nextPinned = !chat.pinned;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chat.id]: {
            ...chat,
            pinned: nextPinned,
            pinnedAt: nextPinned ? action.payload.timestamp : undefined,
          },
        },
      };
    }

    case "TOGGLE_UNREAD": {
      const chat = state.chats[action.payload.chatId];
      if (!chat) return state;
      return {
        ...state,
        chats: { ...state.chats, [chat.id]: { ...chat, unread: !chat.unread } },
      };
    }

    case "RENAME_CHAT": {
      const chat = state.chats[action.payload.chatId];
      const title = action.payload.title.trim();
      if (!chat || !title) return state;
      return {
        ...state,
        chats: {
          ...state.chats,
          [chat.id]: { ...chat, title },
        },
      };
    }

    case "DELETE_CHAT": {
      const { chatId } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;

      const remainingChats = { ...state.chats };
      delete remainingChats[chatId];

      const remainingMessages = { ...state.messages };
      // Drop each message's action proposal too, the same way EDIT_MESSAGE
      // does — otherwise deleting a chat leaves its proposals orphaned in
      // state forever, referenced by nothing.
      const remainingActionProposals = { ...state.actionProposals };
      for (const messageId of chat.messageIds) {
        const removed = remainingMessages[messageId];
        if (removed?.actionProposalId) delete remainingActionProposals[removed.actionProposalId];
        delete remainingMessages[messageId];
      }

      const wasActive = state.activeChatId === chatId;

      // If a task owned this chat, unlink it and drop its run history — every
      // run points at a messageId that no longer exists. The next Run now
      // starts a fresh chat.
      const nextScheduledTasks = { ...state.scheduledTasks };
      for (const task of Object.values(nextScheduledTasks)) {
        if (task.chatId === chatId) {
          nextScheduledTasks[task.id] = { ...task, chatId: undefined, runs: [] };
        }
      }

      return {
        ...state,
        chats: remainingChats,
        chatOrder: state.chatOrder.filter((id) => id !== chatId),
        messages: remainingMessages,
        actionProposals: remainingActionProposals,
        scheduledTasks: nextScheduledTasks,
        activeChatId: wasActive ? null : state.activeChatId,
        draft: wasActive ? freshDraft() : state.draft,
      };
    }

    case "TOGGLE_CONNECTOR": {
      const id = action.payload.connectorId;
      if (state.activeChatId) {
        const chat = state.chats[state.activeChatId];
        const has = chat.connectorIds.includes(id);
        const connectorIds = has
          ? chat.connectorIds.filter((c) => c !== id)
          : [...chat.connectorIds, id];
        return {
          ...state,
          chats: { ...state.chats, [chat.id]: { ...chat, connectorIds } },
        };
      }
      const has = state.draft.connectorIds.includes(id);
      const connectorIds = has
        ? state.draft.connectorIds.filter((c) => c !== id)
        : [...state.draft.connectorIds, id];
      return { ...state, draft: { ...state.draft, connectorIds } };
    }

    case "ADD_ATTACHMENTS":
      return {
        ...state,
        draft: {
          ...state.draft,
          attachments: [...state.draft.attachments, ...action.payload.attachments],
        },
      };

    case "REMOVE_ATTACHMENT":
      return {
        ...state,
        draft: {
          ...state.draft,
          attachments: state.draft.attachments.filter((a) => a.id !== action.payload.id),
        },
      };

    case "TOGGLE_SIDEBAR":
      return { ...state, sidebarCollapsed: !state.sidebarCollapsed };

    case "CREATE_PROJECT": {
      const { projectId, name, description, timestamp } = action.payload;
      const project: Project = {
        id: projectId,
        name,
        description,
        instructionsText: "",
        connectorIds: [],
        fileIds: [],
        createdAt: timestamp,
      };
      return {
        ...state,
        projects: { ...state.projects, [projectId]: project },
        projectOrder: [projectId, ...state.projectOrder],
        workspaceScope: { type: "project", projectId },
        activeChatId: null,
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "ENTER_PROJECT": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      return {
        ...state,
        workspaceScope: { type: "project", projectId: project.id },
        activeChatId: null,
        mainView: "workspace",
        draft: freshDraft(),
      };
    }

    case "SET_PROJECT_INSTRUCTIONS": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: { ...project, instructionsText: action.payload.text },
        },
      };
    }

    case "RENAME_PROJECT": {
      const project = state.projects[action.payload.projectId];
      const name = action.payload.name.trim();
      if (!project || !name) return state;
      const description = action.payload.description?.trim();
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: { ...project, name, description: description || undefined },
        },
      };
    }

    case "TOGGLE_PROJECT_PINNED": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const nextPinned = !project.pinned;
      return {
        ...state,
        projects: {
          ...state.projects,
          [project.id]: {
            ...project,
            pinned: nextPinned,
            pinnedAt: nextPinned ? action.payload.timestamp : undefined,
          },
        },
      };
    }

    case "DELETE_PROJECT": {
      const { projectId } = action.payload;
      const project = state.projects[projectId];
      if (!project) return state;

      const remainingProjects = { ...state.projects };
      delete remainingProjects[projectId];

      const remainingProjectFiles = { ...state.projectFiles };
      for (const fileId of project.fileIds) {
        delete remainingProjectFiles[fileId];
      }

      const remainingScheduledTasks = Object.fromEntries(
        Object.entries(state.scheduledTasks).filter(([, task]) => task.projectId !== projectId),
      );

      const updatedChats = { ...state.chats };
      for (const chat of Object.values(updatedChats)) {
        if (chat.projectId === projectId) {
          updatedChats[chat.id] = { ...chat, projectId: null };
        }
      }

      // Deleting a project also deletes its scheduled tasks — unlink their
      // chats too. Without this a chat is filtered out of Chats (still
      // flagged as task-owned) while its task row is gone, so it disappears
      // from the sidebar entirely.
      for (const task of Object.values(state.scheduledTasks)) {
        if (task.projectId === projectId && task.chatId && updatedChats[task.chatId]) {
          const { scheduledTaskId: _unlinked, ...rest } = updatedChats[task.chatId];
          updatedChats[task.chatId] = rest;
        }
      }

      const wasActiveWorkspace =
        state.workspaceScope.type === "project" && state.workspaceScope.projectId === projectId;

      // Deleting a project also deletes its scheduled tasks — if one of those
      // was open on the detail page, that page would be left showing a
      // "no longer exists" dead end, so fall back to the task list.
      const viewedTaskRemoved =
        state.mainView === "scheduledTaskDetail" &&
        state.activeScheduledTaskId !== null &&
        !remainingScheduledTasks[state.activeScheduledTaskId];

      return {
        ...state,
        projects: remainingProjects,
        projectOrder: state.projectOrder.filter((id) => id !== projectId),
        projectFiles: remainingProjectFiles,
        scheduledTasks: remainingScheduledTasks,
        chats: updatedChats,
        workspaceScope: wasActiveWorkspace ? { type: "general" } : state.workspaceScope,
        mainView: viewedTaskRemoved ? "scheduledTasks" : state.mainView,
      };
    }

    case "TOGGLE_PROJECT_CONNECTOR": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const id = action.payload.connectorId;
      const has = project.connectorIds.includes(id);
      const connectorIds = has
        ? project.connectorIds.filter((c) => c !== id)
        : [...project.connectorIds, id];
      return {
        ...state,
        projects: { ...state.projects, [project.id]: { ...project, connectorIds } },
      };
    }

    case "ADD_PROJECT_FILES": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const nextFiles = { ...state.projectFiles };
      const newIds: string[] = [];
      for (const file of action.payload.files) {
        nextFiles[file.id] = file;
        newIds.push(file.id);
      }
      return {
        ...state,
        projectFiles: nextFiles,
        projects: {
          ...state.projects,
          [project.id]: { ...project, fileIds: [...project.fileIds, ...newIds] },
        },
      };
    }

    case "REMOVE_PROJECT_FILE": {
      const project = state.projects[action.payload.projectId];
      if (!project) return state;
      const { fileId } = action.payload;

      const remainingProjectFiles = { ...state.projectFiles };
      delete remainingProjectFiles[fileId];

      return {
        ...state,
        projectFiles: remainingProjectFiles,
        projects: {
          ...state.projects,
          [project.id]: { ...project, fileIds: project.fileIds.filter((id) => id !== fileId) },
        },
      };
    }

    case "START_TASK_RUN": {
      const { taskId, runId, chatId, messageId, speakerLabel, timestamp, focus } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;

      // Resolve the chat from LIVE state, not the payload. The caller closes
      // over a snapshot of state, so two quick clicks would both arrive
      // carrying a freshly minted chatId and create two chats for one task.
      const existingChat = task.chatId ? state.chats[task.chatId] : undefined;
      const targetChatId = existingChat ? existingChat.id : chatId;

      const nextChats = { ...state.chats };
      let nextChatOrder = state.chatOrder;

      if (existingChat) {
        nextChats[targetChatId] = {
          ...existingChat,
          messageIds: [...existingChat.messageIds, messageId],
          unread: focus ? false : state.activeChatId !== targetChatId,
        };
      } else {
        nextChats[targetChatId] = {
          id: targetChatId,
          title: task.name,
          projectId: task.projectId ?? null,
          scheduledTaskId: task.id,
          pinned: false,
          createdAt: timestamp,
          messageIds: [messageId],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: task.modelId,
          thinkingEffort: DEFAULT_THINKING_EFFORT,
          unread: !focus,
        };
        nextChatOrder = [targetChatId, ...state.chatOrder];
      }

      const run: TaskRun = {
        id: runId,
        startedAt: timestamp,
        status: "running",
        messageId,
        trigger: "manual",
      };

      return {
        ...state,
        chats: nextChats,
        chatOrder: nextChatOrder,
        messages: {
          ...state.messages,
          [messageId]: {
            id: messageId,
            chatId: targetChatId,
            role: "assistant",
            text: "",
            status: "pending",
            speakerLabel,
            createdAt: timestamp,
          },
        },
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: { ...task, chatId: targetChatId, runs: [...task.runs, run] },
        },
        ...(focus
          ? {
              activeChatId: targetChatId,
              mainView: "workspace" as const,
              workspaceScope: task.projectId
                ? ({ type: "project", projectId: task.projectId } as WorkspaceScope)
                : ({ type: "general" } as WorkspaceScope),
              draft: freshDraft(),
            }
          : {}),
      };
    }

    case "COMPLETE_TASK_RUN": {
      const { taskId, runId, summary, skippedConnectorIds } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;
      return {
        ...state,
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: {
            ...task,
            runs: task.runs.map((run) =>
              run.id === runId
                ? { ...run, status: "completed" as const, summary, skippedConnectorIds }
                : run,
            ),
          },
        },
      };
    }

    case "SEED_TASK_RUN": {
      const {
        taskId, runId, chatId, messageId, text, summary, speakerLabel, ranAt, suggestedConnectorIds,
      } = action.payload;
      const task = state.scheduledTasks[taskId];
      if (!task) return state;

      const existingChat = task.chatId ? state.chats[task.chatId] : undefined;
      const targetChatId = existingChat ? existingChat.id : chatId;

      const nextChats = { ...state.chats };
      let nextChatOrder = state.chatOrder;

      if (existingChat) {
        nextChats[targetChatId] = {
          ...existingChat,
          messageIds: [...existingChat.messageIds, messageId],
          unread: state.activeChatId !== targetChatId,
        };
      } else {
        nextChats[targetChatId] = {
          id: targetChatId,
          title: task.name,
          projectId: task.projectId ?? null,
          scheduledTaskId: task.id,
          pinned: false,
          createdAt: ranAt,
          messageIds: [messageId],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: task.modelId,
          thinkingEffort: DEFAULT_THINKING_EFFORT,
          unread: true,
        };
        nextChatOrder = [targetChatId, ...state.chatOrder];
      }

      return {
        ...state,
        chats: nextChats,
        chatOrder: nextChatOrder,
        // Born complete: useProgressiveReveal only animates on a
        // pending -> complete transition, so history renders instantly.
        messages: {
          ...state.messages,
          [messageId]: {
            id: messageId,
            chatId: targetChatId,
            role: "assistant",
            text,
            status: "complete",
            speakerLabel,
            suggestedConnectorIds,
            createdAt: ranAt,
          },
        },
        scheduledTasks: {
          ...state.scheduledTasks,
          [taskId]: {
            ...task,
            chatId: targetChatId,
            runs: [
              ...task.runs,
              { id: runId, startedAt: ranAt, status: "completed", messageId, summary, trigger: "seed" },
            ],
          },
        },
      };
    }

    case "PROPOSE_DISTRIBUTION": {
      const { proposalId, messageId, connectorId, destinationLabel, body } = action.payload;
      const message = state.messages[messageId];
      if (!message) return state;
      return {
        ...state,
        messages: {
          ...state.messages,
          [messageId]: { ...message, distributionProposalId: proposalId },
        },
        actionProposals: {
          ...state.actionProposals,
          [proposalId]: {
            id: proposalId,
            chatId: message.chatId,
            connectorId,
            actionType: "post_to_channel",
            title: `Post to ${destinationLabel}`,
            fields: [
              { label: "Destination", value: destinationLabel },
              { label: "Content", value: "This response" },
            ],
            body,
            status: "pending",
          },
        },
      };
    }

    case "ADD_DRAFT_SOURCE": {
      const { source } = action.payload;
      if (state.draft.sources.some((s) => s.kind === source.kind && s.scope === source.scope)) {
        return state;
      }
      return { ...state, draft: { ...state.draft, sources: [...state.draft.sources, source] } };
    }

    case "REMOVE_DRAFT_SOURCE":
      return {
        ...state,
        draft: {
          ...state.draft,
          sources: state.draft.sources.filter((s) => s.id !== action.payload.id),
        },
      };

    case "CREATE_SCHEDULED_TASK": {
      const task = action.payload;
      if (task.projectId && !state.projects[task.projectId]) return state;

      // Adopt the setup conversation as this task's output chat, so one
      // scheduling conversation yields exactly one sidebar row rather than a
      // chat and a task that look identical.
      const nextChats = { ...state.chats };
      const linked = task.chatId ? state.chats[task.chatId] : undefined;
      if (linked) {
        nextChats[linked.id] = {
          ...linked,
          scheduledTaskId: task.id,
          title: linked.title === "New scheduled task" ? task.name : linked.title,
        };
      }

      return {
        ...state,
        chats: nextChats,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: task },
      };
    }

    case "UPDATE_SCHEDULED_TASK": {
      const task = action.payload;
      const previous = state.scheduledTasks[task.id];
      if (!previous) return state;

      // Keep the owned chat in step with the task, but never overwrite a
      // title the user set themselves.
      const nextChats = { ...state.chats };
      const linked = task.chatId ? state.chats[task.chatId] : undefined;
      if (linked) {
        nextChats[linked.id] = {
          ...linked,
          title: linked.title === previous.name ? task.name : linked.title,
          projectId: task.projectId ?? null,
        };
      }

      return {
        ...state,
        chats: nextChats,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: task },
      };
    }

    case "DELETE_SCHEDULED_TASK": {
      const { id } = action.payload;
      const task = state.scheduledTasks[id];
      if (!task) return state;
      const remainingScheduledTasks = { ...state.scheduledTasks };
      delete remainingScheduledTasks[id];

      // Keep the chat and its past briefs — unlinking drops it back under
      // Chats. Silently destroying a conversation behind a "delete task"
      // confirmation would be a nasty surprise.
      const nextChats = { ...state.chats };
      if (task.chatId && nextChats[task.chatId]) {
        const { scheduledTaskId: _unlinked, ...rest } = nextChats[task.chatId];
        nextChats[task.chatId] = rest;
      }

      const wasViewingIt = state.mainView === "scheduledTaskDetail" && state.activeScheduledTaskId === id;
      return {
        ...state,
        chats: nextChats,
        scheduledTasks: remainingScheduledTasks,
        mainView: wasViewingIt ? "scheduledTasks" : state.mainView,
      };
    }

    case "TOGGLE_SCHEDULED_TASK_ACTIVE": {
      const task = state.scheduledTasks[action.payload.id];
      if (!task) return state;
      return {
        ...state,
        scheduledTasks: { ...state.scheduledTasks, [task.id]: { ...task, active: !task.active } },
      };
    }

    case "OPEN_ALL_PROJECTS":
      return { ...state, mainView: "allProjects" };

    case "CLOSE_ALL_PROJECTS":
      return { ...state, mainView: "workspace" };

    case "OPEN_SCHEDULED_TASKS":
      return { ...state, mainView: "scheduledTasks" };

    case "VIEW_SCHEDULED_TASK":
      return { ...state, mainView: "scheduledTaskDetail", activeScheduledTaskId: action.payload.id };

    case "MOVE_CHAT_TO_PROJECT": {
      const { chatId, projectId, timestamp } = action.payload;
      const chat = state.chats[chatId];
      if (!chat) return state;
      const updatedChat: Chat = {
        ...chat,
        projectId,
        pinnedAt: chat.pinned ? timestamp : chat.pinnedAt,
      };
      const isActive = state.activeChatId === chatId;
      return {
        ...state,
        chats: { ...state.chats, [chatId]: updatedChat },
        workspaceScope: isActive
          ? projectId
            ? { type: "project", projectId }
            : { type: "general" }
          : state.workspaceScope,
      };
    }

    case "CONNECT_CONNECTOR": {
      const connector = state.connectors[action.payload.connectorId];
      if (!connector) return state;
      return {
        ...state,
        connectors: {
          ...state.connectors,
          [connector.id]: { ...connector, state: "connected" },
        },
      };
    }

    case "DISCONNECT_CONNECTOR": {
      const connector = state.connectors[action.payload.connectorId];
      if (!connector) return state;
      return {
        ...state,
        connectors: {
          ...state.connectors,
          [connector.id]: { ...connector, state: "not_connected" },
        },
      };
    }

    case "CREATE_SKILL": {
      const { skillId, name, description, instructions } = action.payload;
      const skill: Skill = { id: skillId, name, description, instructions };
      return {
        ...state,
        skills: { ...state.skills, [skillId]: skill },
        skillOrder: [skillId, ...state.skillOrder],
      };
    }

    case "UPDATE_SKILL": {
      const { skillId, name, description, instructions } = action.payload;
      const existing = state.skills[skillId];
      if (!existing) return state;
      return {
        ...state,
        skills: {
          ...state.skills,
          [skillId]: { ...existing, name, description, instructions },
        },
      };
    }

    case "DELETE_SKILL": {
      const { skillId } = action.payload;
      if (!state.skills[skillId]) return state;

      const remainingSkills = { ...state.skills };
      delete remainingSkills[skillId];

      const updatedChats = { ...state.chats };
      for (const chat of Object.values(updatedChats)) {
        if (chat.activeSkillId === skillId) {
          updatedChats[chat.id] = { ...chat, activeSkillId: null };
        }
      }

      return {
        ...state,
        skills: remainingSkills,
        skillOrder: state.skillOrder.filter((id) => id !== skillId),
        chats: updatedChats,
        draft: state.draft.skillId === skillId ? { ...state.draft, skillId: null } : state.draft,
      };
    }

    case "SET_ACTION_PROPOSAL_STATUS": {
      const proposal = state.actionProposals[action.payload.id];
      if (!proposal) return state;
      return {
        ...state,
        actionProposals: {
          ...state.actionProposals,
          [proposal.id]: {
            ...proposal,
            status: action.payload.status,
            createdTaskId: action.payload.createdTaskId ?? proposal.createdTaskId,
          },
        },
      };
    }

    case "OPEN_SETTINGS":
      return {
        ...state,
        settingsModal: {
          open: true,
          section: action.payload.section ?? state.settingsModal.section,
        },
      };

    case "CLOSE_SETTINGS":
      return { ...state, settingsModal: { ...state.settingsModal, open: false } };

    case "SET_SETTINGS_SECTION":
      return {
        ...state,
        settingsModal: { ...state.settingsModal, section: action.payload.section },
      };

    case "OPEN_PROJECT_SETTINGS":
      return {
        ...state,
        projectSettingsModal: {
          open: true,
          section: action.payload.section ?? state.projectSettingsModal.section,
        },
      };

    case "CLOSE_PROJECT_SETTINGS":
      return {
        ...state,
        projectSettingsModal: { ...state.projectSettingsModal, open: false },
      };

    case "SET_PROJECT_SETTINGS_SECTION":
      return {
        ...state,
        projectSettingsModal: { ...state.projectSettingsModal, section: action.payload.section },
      };

    default:
      return state;
  }
}

interface AppContextValue {
  state: AppState;
  activeChat: Chat | null;
  activeMessages: Message[];
  activeSkillId: string | null;
  activeConnectorIds: string[];
  activeModelId: string;
  activeThinkingEffort: ThinkingEffort;
  chatList: Chat[];
  projects: Project[];
  activeProject: Project | null;
  connectorList: Connector[];
  skillList: Skill[];
  sendMessage: (overrideText?: string) => void;
  startScheduledTaskSetup: (options?: { seedPrompt?: string }) => void;
  regenerateMessage: (messageId: string) => void;
  editMessage: (messageId: string, newText: string) => void;
  newChat: () => void;
  selectChat: (chatId: string) => void;
  setDraftText: (text: string) => void;
  setThinkingEffort: (value: ThinkingEffort) => void;
  setSkill: (skillId: string | null) => void;
  setModel: (modelId: string) => void;
  togglePin: (chatId: string) => void;
  toggleUnread: (chatId: string) => void;
  renameChat: (chatId: string, title: string) => void;
  deleteChat: (chatId: string) => void;
  toggleConnector: (connectorId: string) => void;
  addAttachments: (attachments: Attachment[]) => void;
  removeAttachment: (id: string) => void;
  toggleSidebar: () => void;
  createProject: (name: string, description?: string) => void;
  enterProject: (projectId: string) => void;
  setProjectInstructions: (projectId: string, text: string) => void;
  renameProject: (projectId: string, name: string, description?: string) => void;
  deleteProject: (projectId: string) => void;
  toggleProjectPinned: (projectId: string) => void;
  toggleProjectConnector: (projectId: string, connectorId: string) => void;
  addProjectFiles: (projectId: string, files: ProjectFile[]) => void;
  removeProjectFile: (projectId: string, fileId: string) => void;
  updateScheduledTask: (task: ScheduledTask) => void;
  runScheduledTask: (taskId: string, options?: { focus?: boolean }) => void;
  addDraftSource: (source: TaskSource) => void;
  removeDraftSource: (id: string) => void;
  proposeDistribution: (messageId: string, destinationId: string) => void;
  deleteScheduledTask: (id: string) => void;
  toggleScheduledTaskActive: (id: string) => void;
  openAllProjects: () => void;
  closeAllProjects: () => void;
  openScheduledTasks: () => void;
  viewScheduledTask: (id: string) => void;
  moveChatToProject: (chatId: string, projectId: string | null) => void;
  connectConnector: (connectorId: string) => void;
  disconnectConnector: (connectorId: string) => void;
  createSkill: (input: { name: string; description: string; instructions: string }) => void;
  updateSkill: (
    skillId: string,
    input: { name: string; description: string; instructions: string },
  ) => void;
  deleteSkill: (skillId: string) => void;
  approveAction: (id: string) => void;
  cancelAction: (id: string) => void;
  openSettings: (section?: SettingsSection) => void;
  closeSettings: () => void;
  setSettingsSection: (section: SettingsSection) => void;
  openProjectSettings: (section?: ProjectSettingsSection) => void;
  closeProjectSettings: () => void;
  setProjectSettingsSection: (section: ProjectSettingsSection) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const ASSISTANT_DELAY_MS = 900;

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const sendMessage = useCallback((overrideText?: string) => {
    const typedText = (overrideText ?? state.draft.text).trim();

    // The composer already intercepts long *pastes* and turns them into a
    // "Pasted text.txt" attachment before they ever reach draft.text (see
    // PromptComposer's onPaste handler) — its content is folded back in here
    // so the message's full text always reflects what was actually said,
    // regardless of how much of it lives in an attachment.
    const existingPastedAttachments = state.draft.attachments.filter((a) => a.isPastedText);
    const pastedContent = existingPastedAttachments.map((a) => a.content ?? "").join("\n\n");
    const rawText = [typedText, pastedContent].filter(Boolean).join("\n\n");
    if (!rawText && state.draft.attachments.length === 0) return;

    // Anything not already caught at paste time — typed directly, or pasted
    // through a path the composer didn't intercept — still gets converted
    // here as a fallback, so a message is represented the same way no
    // matter how the long text arrived.
    const typedIsLongPaste = typedText.length > LONG_PASTE_THRESHOLD;
    const attachments = typedIsLongPaste
      ? [
          ...state.draft.attachments,
          {
            id: createId("attachment"),
            kind: "file" as const,
            name: "Pasted text.txt",
            meta: `${typedText.length.toLocaleString()} characters`,
            isPastedText: true,
            content: typedText,
          },
        ]
      : state.draft.attachments;

    const chatId = state.activeChatId ?? createId("chat");
    const isNewChat = state.activeChatId === null;
    const userMessageId = createId("msg");
    const assistantMessageId = createId("msg");
    const timestamp = Date.now();

    // A chat enters a deterministic demo script only when its opening prompt
    // matches one of the demo triggers; every later message in that chat just
    // advances to the next scripted assistant turn.
    const existingDemoRun = state.chats[chatId]?.demoRun;
    const newChatDemoRun: DemoRun | undefined = isNewChat
      ? (() => {
          const scenarioId = matchDemoScenario(rawText);
          return scenarioId ? { scenarioId, step: 0 } : undefined;
        })()
      : undefined;
    const activeDemoRun = isNewChat ? newChatDemoRun : existingDemoRun;

    const draftSources = state.draft.sources;

    dispatch({
      type: "SEND_MESSAGE",
      payload: {
        chatId,
        isNewChat,
        userMessageId,
        assistantMessageId,
        text: rawText,
        attachments,
        sources: draftSources,
        timestamp,
        demoRun: newChatDemoRun,
      },
    });

    // Attaching a chat room is an explicit instruction to go read it, so it
    // takes precedence over the generic keyword heuristics.
    if (draftSources.length > 0) {
      const reads = readSources(draftSources, state.connectors);
      const activeSkillId = state.chats[chatId]?.activeSkillId ?? state.draft.skillId;
      const text = composeSourceBrief(reads, rawText, activeSkillId);
      const skipped = skippedConnectorIds(reads);
      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: {
            messageId: assistantMessageId,
            text,
            suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
          },
        });
      }, ASSISTANT_DELAY_MS);
      return;
    }

    if (activeDemoRun) {
      const script = DEMO_SCRIPTS[activeDemoRun.scenarioId];
      if (activeDemoRun.step < script.length) {
        const turn = script[activeDemoRun.step];
        window.setTimeout(() => {
          dispatch({
            type: "COMPLETE_ASSISTANT_MESSAGE",
            payload: {
              messageId: assistantMessageId,
              text: turn.text,
              citations: turn.citations,
              demoStep: activeDemoRun.step + 1,
            },
          });
        }, ASSISTANT_DELAY_MS);
        return;
      }
    }

    const activeProjectId = state.workspaceScope.type === "project" ? state.workspaceScope.projectId : null;
    const project = activeProjectId ? state.projects[activeProjectId] : null;
    const connector = state.connectors[ACTION_CONNECTOR_ID];
    const connectorAvailable =
      connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

    const response = generateMockAssistantResponse(rawText, {
      connectorAvailable: Boolean(connectorAvailable),
      connectorName: connector?.name ?? "Outlook",
      projectName: project?.name ?? null,
      connectedConnectorIds: connectedConnectorIds(state.connectors),
      forceSchedule: existingDemoRun?.scenarioId === "schedule-setup",
    });

    window.setTimeout(() => {
      dispatchMockResponse(dispatch, assistantMessageId, response, connector);
    }, ASSISTANT_DELAY_MS);
  }, [
    state.draft.text,
    state.draft.attachments,
    state.draft.sources,
    state.draft.skillId,
    state.activeChatId,
    state.workspaceScope,
    state.projects,
    state.connectors,
    state.chats,
  ]);

  /**
   * Always starts a brand-new chat, regardless of whatever was active before
   * — "New task" should never land the setup conversation inside an unrelated
   * project or thread.
   *
   * With a `seedPrompt` (a template) the intro turn is skipped and the
   * schedule proposal is produced straight away, so one click lands on a
   * filled-in card ready to approve. Without one, the agent introduces itself
   * and asks what to set up.
   */
  const startScheduledTaskSetup = useCallback(
    (options?: { seedPrompt?: string }) => {
      const chatId = createId("chat");
      const userMessageId = createId("msg");
      const assistantMessageId = createId("msg");
      const timestamp = Date.now();
      const seedPrompt = options?.seedPrompt?.trim();

      dispatch({
        type: "START_SCHEDULING_CHAT",
        payload: {
          chatId,
          userMessageId,
          assistantMessageId,
          text: seedPrompt || SCHEDULE_SETUP_OPENING_PROMPT,
          timestamp,
        },
      });

      if (seedPrompt) {
        const response = generateMockAssistantResponse(seedPrompt, {
          connectorAvailable: true,
          connectorName: "Outlook",
          projectName: null,
          connectedConnectorIds: connectedConnectorIds(state.connectors),
          forceSchedule: true,
        });
        window.setTimeout(() => {
          dispatchMockResponse(dispatch, assistantMessageId, response, undefined);
        }, ASSISTANT_DELAY_MS);
        return;
      }

      const introTurn = DEMO_SCRIPTS["schedule-setup"][0];
      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: { messageId: assistantMessageId, text: introTurn.text, demoStep: 1 },
        });
      }, ASSISTANT_DELAY_MS);
    },
    // Reads connector state when seeding a template's schedule proposal.
    [state.connectors],
  );

  const regenerateMessage = useCallback(
    (messageId: string) => {
      const message = state.messages[messageId];
      if (!message || message.role !== "assistant") return;
      const chat = state.chats[message.chatId];
      if (!chat) return;

      const messageIndex = chat.messageIds.indexOf(messageId);
      const precedingUserMessageId = messageIndex > 0 ? chat.messageIds[messageIndex - 1] : null;
      const promptText = precedingUserMessageId ? (state.messages[precedingUserMessageId]?.text ?? "") : "";

      dispatch({ type: "REGENERATE_MESSAGE", payload: { messageId } });

      // Demo-scripted chats replay that turn's canned text again rather than
      // falling back to the generic mock logic — matched by this message's
      // position among the chat's assistant turns (not the chat's current
      // step, which would only be correct when regenerating the latest one).
      if (chat.demoRun) {
        const script = DEMO_SCRIPTS[chat.demoRun.scenarioId];
        const assistantTurnIds = chat.messageIds.filter((id) => state.messages[id]?.role === "assistant");
        const turn = script[assistantTurnIds.indexOf(messageId)];
        if (turn) {
          window.setTimeout(() => {
            dispatch({
              type: "COMPLETE_ASSISTANT_MESSAGE",
              payload: { messageId, text: turn.text, citations: turn.citations },
            });
          }, ASSISTANT_DELAY_MS);
          return;
        }
      }

      const project = chat.projectId ? state.projects[chat.projectId] : null;
      const connector = state.connectors[ACTION_CONNECTOR_ID];
      const connectorAvailable =
        connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

      const response = generateMockAssistantResponse(promptText, {
        connectorAvailable: Boolean(connectorAvailable),
        connectorName: connector?.name ?? "Outlook",
        projectName: project?.name ?? null,
        connectedConnectorIds: connectedConnectorIds(state.connectors),
        forceSchedule: chat.demoRun?.scenarioId === "schedule-setup",
      });

      window.setTimeout(() => {
        dispatchMockResponse(dispatch, messageId, response, connector);
      }, ASSISTANT_DELAY_MS);
    },
    [state.messages, state.chats, state.projects, state.connectors],
  );

  const editMessage = useCallback(
    (messageId: string, newText: string) => {
      const trimmed = newText.trim();
      if (!trimmed) return;
      const message = state.messages[messageId];
      if (!message || message.role !== "user") return;
      const chat = state.chats[message.chatId];
      if (!chat) return;

      const assistantMessageId = createId("msg");
      const isFirstMessage = chat.messageIds[0] === messageId;

      dispatch({
        type: "EDIT_MESSAGE",
        payload: { chatId: chat.id, messageId, text: trimmed, assistantMessageId, retitle: isFirstMessage },
      });

      // Demo scripts are keyed to the chat's original opening prompt, so an
      // edit (even to the first message) always falls back to the generic
      // mock response rather than trying to resume a script mid-way.
      const project = chat.projectId ? state.projects[chat.projectId] : null;
      const connector = state.connectors[ACTION_CONNECTOR_ID];
      const connectorAvailable =
        connector?.state === "connected" && (!project || project.connectorIds.includes(ACTION_CONNECTOR_ID));

      const response = generateMockAssistantResponse(trimmed, {
        connectorAvailable: Boolean(connectorAvailable),
        connectorName: connector?.name ?? "Outlook",
        projectName: project?.name ?? null,
        connectedConnectorIds: connectedConnectorIds(state.connectors),
      });

      window.setTimeout(() => {
        dispatchMockResponse(dispatch, assistantMessageId, response, connector);
      }, ASSISTANT_DELAY_MS);
    },
    [state.messages, state.chats, state.projects, state.connectors],
  );

  const newChat = useCallback(() => {
    // Already sitting on a fresh, empty general chat — clicking New chat
    // again shouldn't wipe an in-progress draft for no reason. Nudge the
    // composer instead so it's clear nothing needs to change.
    const alreadyFreshChat =
      state.activeChatId === null &&
      state.mainView === "workspace" &&
      state.workspaceScope.type === "general";
    if (alreadyFreshChat) {
      dispatch({ type: "NUDGE_COMPOSER", payload: { timestamp: Date.now() } });
      return;
    }
    dispatch({ type: "NEW_CHAT" });
  }, [state.activeChatId, state.mainView, state.workspaceScope]);
  const selectChat = useCallback(
    (chatId: string) => dispatch({ type: "SELECT_CHAT", payload: { chatId } }),
    [],
  );
  const setDraftText = useCallback(
    (text: string) => dispatch({ type: "SET_DRAFT_TEXT", payload: { text } }),
    [],
  );
  const setThinkingEffort = useCallback(
    (value: ThinkingEffort) => dispatch({ type: "SET_THINKING_EFFORT", payload: { value } }),
    [],
  );
  const setSkill = useCallback(
    (skillId: string | null) => dispatch({ type: "SET_SKILL", payload: { skillId } }),
    [],
  );
  const setModel = useCallback(
    (modelId: string) => dispatch({ type: "SET_MODEL", payload: { modelId } }),
    [],
  );
  const togglePin = useCallback(
    (chatId: string) =>
      dispatch({ type: "TOGGLE_PIN", payload: { chatId, timestamp: Date.now() } }),
    [],
  );
  const toggleUnread = useCallback(
    (chatId: string) => dispatch({ type: "TOGGLE_UNREAD", payload: { chatId } }),
    [],
  );
  const renameChat = useCallback(
    (chatId: string, title: string) => dispatch({ type: "RENAME_CHAT", payload: { chatId, title } }),
    [],
  );
  const deleteChat = useCallback(
    (chatId: string) => dispatch({ type: "DELETE_CHAT", payload: { chatId } }),
    [],
  );
  const toggleConnector = useCallback(
    (connectorId: string) => dispatch({ type: "TOGGLE_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const addAttachments = useCallback(
    (attachments: Attachment[]) => dispatch({ type: "ADD_ATTACHMENTS", payload: { attachments } }),
    [],
  );
  const removeAttachment = useCallback(
    (id: string) => dispatch({ type: "REMOVE_ATTACHMENT", payload: { id } }),
    [],
  );
  const toggleSidebar = useCallback(() => dispatch({ type: "TOGGLE_SIDEBAR" }), []);

  const createProject = useCallback(
    (name: string, description?: string) =>
      dispatch({
        type: "CREATE_PROJECT",
        payload: { projectId: createId("project"), name, description, timestamp: Date.now() },
      }),
    [],
  );
  const enterProject = useCallback(
    (projectId: string) => dispatch({ type: "ENTER_PROJECT", payload: { projectId } }),
    [],
  );
  const setProjectInstructions = useCallback(
    (projectId: string, text: string) =>
      dispatch({ type: "SET_PROJECT_INSTRUCTIONS", payload: { projectId, text } }),
    [],
  );
  const renameProject = useCallback(
    (projectId: string, name: string, description?: string) =>
      dispatch({ type: "RENAME_PROJECT", payload: { projectId, name, description } }),
    [],
  );
  const deleteProject = useCallback(
    (projectId: string) => dispatch({ type: "DELETE_PROJECT", payload: { projectId } }),
    [],
  );
  const toggleProjectPinned = useCallback(
    (projectId: string) =>
      dispatch({ type: "TOGGLE_PROJECT_PINNED", payload: { projectId, timestamp: Date.now() } }),
    [],
  );
  const toggleProjectConnector = useCallback(
    (projectId: string, connectorId: string) =>
      dispatch({ type: "TOGGLE_PROJECT_CONNECTOR", payload: { projectId, connectorId } }),
    [],
  );
  const addProjectFiles = useCallback(
    (projectId: string, files: ProjectFile[]) =>
      dispatch({ type: "ADD_PROJECT_FILES", payload: { projectId, files } }),
    [],
  );
  const removeProjectFile = useCallback(
    (projectId: string, fileId: string) =>
      dispatch({ type: "REMOVE_PROJECT_FILE", payload: { projectId, fileId } }),
    [],
  );
  /** Generates a task's output and deposits it into the chat that task owns.
   * `focus` navigates there; without it the chat just picks up an unread dot. */
  const runScheduledTask = useCallback(
    (taskId: string, options?: { focus?: boolean }) => {
      const task = state.scheduledTasks[taskId];
      if (!task) return;

      const runId = createId("run");
      const messageId = createId("msg");
      const timestamp = Date.now();

      dispatch({
        type: "START_TASK_RUN",
        payload: {
          taskId,
          runId,
          chatId: createId("chat"),
          messageId,
          speakerLabel: formatRunLabel(timestamp),
          timestamp,
          focus: options?.focus ?? false,
        },
      });

      const reads = readSources(task.sources, state.connectors);
      const { text, summary } = composeRunMessage(task, reads, timestamp);
      const skipped = skippedConnectorIds(reads);

      // A run never posts on its own — even under "auto_run" it only offers,
      // and the user approves. Only reachable destinations are offered.
      const reachable = task.distributions
        .map((d) => findDestinationByValue(d.kind, d.target))
        .filter(
          (option): option is NonNullable<typeof option> =>
            !!option && state.connectors[option.connectorId]?.state === "connected",
        );

      window.setTimeout(() => {
        dispatch({
          type: "COMPLETE_ASSISTANT_MESSAGE",
          payload: {
            messageId,
            text,
            suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
            actionProposal:
              reachable.length > 0
                ? {
                    id: createId("action"),
                    connectorId: reachable[0].connectorId,
                    actionType: "post_to_channel",
                    title: `Post to ${reachable.map((o) => o.label).join(" and ")}`,
                    fields: [
                      { label: "Destination", value: reachable.map((o) => o.label).join(", ") },
                      { label: "Content", value: `${task.name} — this run` },
                    ],
                    body: previewForDistribution(text),
                  }
                : undefined,
          },
        });
        dispatch({
          type: "COMPLETE_TASK_RUN",
          payload: { taskId, runId, summary, skippedConnectorIds: skipped },
        });
      }, ASSISTANT_DELAY_MS);
    },
    [state.scheduledTasks, state.connectors],
  );

  /** One backdated, already-complete run, so a freshly created task has
   * something to show. Never seeded for "manual" — a task that has never been
   * triggered must not claim it ran. */
  const seedTaskRun = useCallback(
    (task: ScheduledTask) => {
      const ranAt = previousOccurrence(task, Date.now());
      if (ranAt === null) return;

      const reads = readSources(task.sources, state.connectors);
      const { text, summary } = composeRunMessage(task, reads, ranAt);
      const skipped = skippedConnectorIds(reads);

      dispatch({
        type: "SEED_TASK_RUN",
        payload: {
          taskId: task.id,
          runId: createId("run"),
          chatId: createId("chat"),
          messageId: createId("msg"),
          text,
          summary,
          speakerLabel: formatRunLabel(ranAt),
          ranAt,
          suggestedConnectorIds: skipped.length > 0 ? skipped : undefined,
        },
      });
    },
    [state.connectors],
  );


  /** Raises a "post this response to X" proposal. Nothing leaves the app —
   * approval runs the same simulated cascade every other action does. */
  const proposeDistribution = useCallback(
    (messageId: string, destinationId: string) => {
      const message = state.messages[messageId];
      const destination = findDestination(destinationId);
      if (!message || !destination) return;

      dispatch({
        type: "PROPOSE_DISTRIBUTION",
        payload: {
          proposalId: createId("action"),
          messageId,
          connectorId: destination.connectorId,
          destinationLabel: destination.label,
          body: previewForDistribution(message.text),
        },
      });
    },
    [state.messages],
  );

  const addDraftSource = useCallback(
    (source: TaskSource) => dispatch({ type: "ADD_DRAFT_SOURCE", payload: { source } }),
    [],
  );
  const removeDraftSource = useCallback(
    (id: string) => dispatch({ type: "REMOVE_DRAFT_SOURCE", payload: { id } }),
    [],
  );
  const updateScheduledTask = useCallback(
    (task: ScheduledTask) => dispatch({ type: "UPDATE_SCHEDULED_TASK", payload: task }),
    [],
  );
  const deleteScheduledTask = useCallback(
    (id: string) => dispatch({ type: "DELETE_SCHEDULED_TASK", payload: { id } }),
    [],
  );
  const toggleScheduledTaskActive = useCallback(
    (id: string) => dispatch({ type: "TOGGLE_SCHEDULED_TASK_ACTIVE", payload: { id } }),
    [],
  );
  const openAllProjects = useCallback(() => dispatch({ type: "OPEN_ALL_PROJECTS" }), []);
  const closeAllProjects = useCallback(() => dispatch({ type: "CLOSE_ALL_PROJECTS" }), []);
  const openScheduledTasks = useCallback(() => dispatch({ type: "OPEN_SCHEDULED_TASKS" }), []);
  const viewScheduledTask = useCallback(
    (id: string) => dispatch({ type: "VIEW_SCHEDULED_TASK", payload: { id } }),
    [],
  );
  const moveChatToProject = useCallback(
    (chatId: string, projectId: string | null) =>
      dispatch({
        type: "MOVE_CHAT_TO_PROJECT",
        payload: { chatId, projectId, timestamp: Date.now() },
      }),
    [],
  );

  const connectConnector = useCallback(
    (connectorId: string) => dispatch({ type: "CONNECT_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const disconnectConnector = useCallback(
    (connectorId: string) => dispatch({ type: "DISCONNECT_CONNECTOR", payload: { connectorId } }),
    [],
  );
  const createSkill = useCallback(
    (input: { name: string; description: string; instructions: string }) =>
      dispatch({ type: "CREATE_SKILL", payload: { skillId: createId("skill"), ...input } }),
    [],
  );
  const updateSkill = useCallback(
    (skillId: string, input: { name: string; description: string; instructions: string }) =>
      dispatch({ type: "UPDATE_SKILL", payload: { skillId, ...input } }),
    [],
  );
  const deleteSkill = useCallback(
    (skillId: string) => dispatch({ type: "DELETE_SKILL", payload: { skillId } }),
    [],
  );

  const approveAction = useCallback(
    (id: string) => {
      const proposal = state.actionProposals[id];
      dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "approved" } });
      window.setTimeout(() => {
        dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "processing" } });
        window.setTimeout(() => {
          // Scheduling is the one proposal type that actually produces a
          // real (mocked) side effect — every other action type just plays
          // out the generic approve/process/complete timeline.
          let createdTaskId: string | undefined;
          if (proposal?.actionType === "schedule_task" && proposal.scheduledTaskDraft) {
            createdTaskId = createId("task");
            const task: ScheduledTask = {
              id: createdTaskId,
              createdAt: Date.now(),
              name: proposal.scheduledTaskDraft.name,
              instructions: proposal.scheduledTaskDraft.instructions,
              modelId: DEFAULT_MODEL_ID,
              frequency: proposal.scheduledTaskDraft.frequency,
              timeOfDay: proposal.scheduledTaskDraft.timeOfDay,
              sources: proposal.scheduledTaskDraft.sources,
              distributions: [],
              permission: "manual_approve",
              // The conversation that set this up becomes the task's output
              // chat, so approving yields one sidebar row rather than two.
              chatId: proposal.chatId,
              runs: [],
              active: true,
            };
            // CREATE_SCHEDULED_TASK adopts and renames the chat itself — doing
            // it in the reducer keeps it atomic instead of reading this
            // callback's stale `state` snapshot.
            dispatch({ type: "CREATE_SCHEDULED_TASK", payload: task });
            seedTaskRun(task);
          }
          dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "completed", createdTaskId } });
        }, 900);
      }, 350);
    },
    // seedTaskRun is recreated whenever connectors change; without it here,
    // connect-then-approve would seed the run from pre-connect state. Note
    // state.chats is NOT needed — chat adoption moved into the reducer.
    [state.actionProposals, seedTaskRun],
  );
  const cancelAction = useCallback(
    (id: string) => dispatch({ type: "SET_ACTION_PROPOSAL_STATUS", payload: { id, status: "cancelled" } }),
    [],
  );
  const openSettings = useCallback(
    (section?: SettingsSection) => dispatch({ type: "OPEN_SETTINGS", payload: { section } }),
    [],
  );
  const closeSettings = useCallback(() => dispatch({ type: "CLOSE_SETTINGS" }), []);
  const setSettingsSection = useCallback(
    (section: SettingsSection) => dispatch({ type: "SET_SETTINGS_SECTION", payload: { section } }),
    [],
  );
  const openProjectSettings = useCallback(
    (section?: ProjectSettingsSection) =>
      dispatch({ type: "OPEN_PROJECT_SETTINGS", payload: { section } }),
    [],
  );
  const closeProjectSettings = useCallback(() => dispatch({ type: "CLOSE_PROJECT_SETTINGS" }), []);
  const setProjectSettingsSection = useCallback(
    (section: ProjectSettingsSection) =>
      dispatch({ type: "SET_PROJECT_SETTINGS_SECTION", payload: { section } }),
    [],
  );

  const activeChat = state.activeChatId ? state.chats[state.activeChatId] ?? null : null;

  const activeMessages = useMemo(() => {
    if (!activeChat) return [];
    return activeChat.messageIds.map((id) => state.messages[id]).filter(Boolean);
  }, [activeChat, state.messages]);

  const activeSkillId = activeChat ? activeChat.activeSkillId : state.draft.skillId;
  const activeConnectorIds = activeChat ? activeChat.connectorIds : state.draft.connectorIds;
  const activeModelId = activeChat ? activeChat.selectedModelId : state.draft.modelId;
  const activeThinkingEffort = activeChat ? activeChat.thinkingEffort : state.draft.thinkingEffort;

  const chatList = useMemo(
    () => state.chatOrder.map((id) => state.chats[id]).filter(Boolean),
    [state.chatOrder, state.chats],
  );

  const projects = useMemo(
    () => state.projectOrder.map((id) => state.projects[id]).filter(Boolean),
    [state.projectOrder, state.projects],
  );

  const activeProject =
    state.workspaceScope.type === "project" ? state.projects[state.workspaceScope.projectId] ?? null : null;

  const connectorList = useMemo(
    () => MOCK_CONNECTORS.map((c) => state.connectors[c.id] ?? c),
    [state.connectors],
  );

  const skillList = useMemo(
    () => state.skillOrder.map((id) => state.skills[id]).filter(Boolean),
    [state.skillOrder, state.skills],
  );

  const value: AppContextValue = {
    state,
    activeChat,
    activeMessages,
    activeSkillId,
    activeConnectorIds,
    activeModelId,
    activeThinkingEffort,
    chatList,
    projects,
    activeProject,
    connectorList,
    skillList,
    sendMessage,
    startScheduledTaskSetup,
    regenerateMessage,
    editMessage,
    newChat,
    selectChat,
    setDraftText,
    setThinkingEffort,
    setSkill,
    setModel,
    togglePin,
    toggleUnread,
    renameChat,
    deleteChat,
    toggleConnector,
    addAttachments,
    removeAttachment,
    toggleSidebar,
    createProject,
    enterProject,
    setProjectInstructions,
    renameProject,
    deleteProject,
    toggleProjectPinned,
    toggleProjectConnector,
    addProjectFiles,
    removeProjectFile,
    updateScheduledTask,
    runScheduledTask,
    addDraftSource,
    removeDraftSource,
    proposeDistribution,
    deleteScheduledTask,
    toggleScheduledTaskActive,
    openAllProjects,
    closeAllProjects,
    openScheduledTasks,
    viewScheduledTask,
    moveChatToProject,
    connectConnector,
    disconnectConnector,
    createSkill,
    updateSkill,
    deleteSkill,
    approveAction,
    cancelAction,
    openSettings,
    closeSettings,
    setSettingsSection,
    openProjectSettings,
    closeProjectSettings,
    setProjectSettingsSection,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppState() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
