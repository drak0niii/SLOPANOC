import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import type { Chat, Message as MessageType } from "../../types";
import type { SourceReferenceDTO } from "../../api/types";

// Message.tsx (and the assistant-actions subtree it renders once a message
// is "complete") reads several things off useAppState() — mocked at the
// module boundary so this file can test Message's own rendering logic in
// isolation, without driving a full AppStateProvider through real
// dispatches/network calls just to get into a given state shape.
const mockAppState: {
  activeChat: Chat | null;
  regenerateMessage: ReturnType<typeof vi.fn>;
  editMessage: ReturnType<typeof vi.fn>;
  setDraftText: ReturnType<typeof vi.fn>;
  proposeDistribution: ReturnType<typeof vi.fn>;
  openSettings: ReturnType<typeof vi.fn>;
  approvePendingAction: ReturnType<typeof vi.fn>;
  rejectPendingAction: ReturnType<typeof vi.fn>;
  toggleRunTraceExpanded: ReturnType<typeof vi.fn>;
  state: { connectors: Record<string, unknown>; draft: { text: string; attachments: unknown[] }; chats: Record<string, Chat> };
} = {
  activeChat: null,
  regenerateMessage: vi.fn(),
  editMessage: vi.fn(),
  setDraftText: vi.fn(),
  proposeDistribution: vi.fn(),
  openSettings: vi.fn(),
  approvePendingAction: vi.fn(),
  rejectPendingAction: vi.fn(),
  toggleRunTraceExpanded: vi.fn(),
  state: { connectors: {}, draft: { text: "", attachments: [] }, chats: {} },
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { Message } from "./Message";
import { TooltipProvider } from "../ui/Tooltip";

// Several message-action buttons render a Radix Tooltip, which requires a
// TooltipProvider ancestor (the real app has one at ProductApp.tsx's
// root) — provide the same wrapper here rather than mocking Tooltip away.
// `withProvider` is reused for both the initial render and every
// `rerender` call, since RTL's rerender replaces the whole tree at the
// root (a bare <Message/> on rerender would drop the provider again).
function withProvider(element: ReactElement) {
  return <TooltipProvider>{element}</TooltipProvider>;
}

function makeChat(overrides: Partial<Chat> = {}): Chat {
  return {
    id: "chat-1",
    title: "Chat",
    projectId: null,
    pinned: false,
    createdAt: 0,
    messageIds: [],
    activeSkillId: null,
    connectorIds: [],
    selectedModelId: "default",
    thinkingEffort: "instant",
    ...overrides,
  };
}

function makeMessage(overrides: Partial<MessageType> = {}): MessageType {
  return {
    id: "msg-1",
    chatId: "chat-1",
    role: "assistant",
    text: "",
    status: "pending",
    createdAt: 0,
    ...overrides,
  };
}

beforeEach(() => {
  vi.useRealTimers();
  mockAppState.activeChat = null;
  mockAppState.state.chats = {};
  mockAppState.regenerateMessage.mockClear();
  mockAppState.editMessage.mockClear();
  mockAppState.toggleRunTraceExpanded.mockClear();
});

describe("Message — mock-path word reveal (regression)", () => {
  it("reveals a pending→complete mock message word-by-word", () => {
    vi.useFakeTimers();
    mockAppState.activeChat = makeChat(); // no backendSessionId — a mock chat

    const message = makeMessage({ status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={message} />));

    const completed = makeMessage({ status: "complete", text: "The full mock answer arrived at once." });
    rerender(withProvider(<Message message={completed} />));

    // Immediately after the transition, the reveal has only shown the
    // first couple of words, not the full text yet.
    expect(screen.queryByText(completed.text)).not.toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText(completed.text)).toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("Message — real backend streaming never triggers the word reveal", () => {
  it("shows the full current text immediately at every streaming step, never a partial/truncated one", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });

    const pending = makeMessage({ status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={pending} />));

    const step1 = makeMessage({ status: "streaming", text: "The" });
    rerender(withProvider(<Message message={step1} />));
    expect(screen.getByText("The")).toBeInTheDocument();

    const step2 = makeMessage({ status: "streaming", text: "The evidence" });
    rerender(withProvider(<Message message={step2} />));
    expect(screen.getByText("The evidence")).toBeInTheDocument();

    const completed = makeMessage({ status: "complete", text: "The evidence indicates a config change." });
    rerender(withProvider(<Message message={completed} />));
    expect(screen.getByText("The evidence indicates a config change.")).toBeInTheDocument();
  });

  it("shows the neutral ThinkingIndicator (no text) before the first status event, never a hardcoded label", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const pending = makeMessage({ status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows the backend's own current activity label, once one arrives, in place of the neutral indicator", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: {
        runToken: "r1",
        assistantMessageId: "msg-1",
        currentActivity: {
          stage: "teams_context",
          label: "Retrieving recent messages from the selected Teams conversation",
        },
        runStartedAt: Date.now(),
      },
    });
    const pending = makeMessage({ status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getByRole("status")).toHaveTextContent(
      "Retrieving recent messages from the selected Teams conversation",
    );
  });

  it("shows a neutral 'Thinking…' label with an elapsed counter once a run has started but before any backend status arrives", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getByRole("status")).toHaveTextContent("Thinking…");
  });

  it("removes the elapsed/activity indicator once the run errors — status flips away from pending", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    // BACKEND_RUN_ERROR flips the message's own status to "error" — the
    // activity/elapsed indicator is gated on status === "pending", so it
    // is gone the instant that happens, regardless of chat.run.
    const errored = makeMessage({ id: "msg-1", status: "error", text: "", errorMessage: "Connection lost." });
    render(withProvider(<Message message={errored} />));

    // Exactly one status region remains -- the error notice -- never the
    // activity/elapsed indicator (which is gone entirely).
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Connection lost.");
    expect(screen.queryByText(/·\s*\d/)).not.toBeInTheDocument();
  });

  it("never persists the elapsed indicator into a completed message", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Done." });
    render(withProvider(<Message message={completed} />));

    expect(screen.queryByText(/·\s*\d/)).not.toBeInTheDocument();
  });

  it("only one activity line renders at a time — no stacked statuses", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: {
        runToken: "r1",
        assistantMessageId: "msg-1",
        currentActivity: { stage: "teams_context", label: "Reviewing the selected Teams conversation" },
        runStartedAt: Date.now(),
      },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});

describe("Message — error state", () => {
  it("renders the error notice, never ThinkingIndicator, and preserves any partial text", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const errored = makeMessage({ status: "error", text: "Partial answer", errorMessage: "Connection lost." });
    render(withProvider(<Message message={errored} />));

    expect(screen.getByText("Partial answer")).toBeInTheDocument();
    expect(screen.getByText("Connection lost.")).toBeInTheDocument();
  });

  it("falls back to a generic message when errorMessage is absent", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const errored = makeMessage({ status: "error", text: "" });
    render(withProvider(<Message message={errored} />));

    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });
});

describe("Message — Regenerate visibility", () => {
  it("hides Regenerate for a backend-sourced completed message", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const completed = makeMessage({ status: "complete", text: "A real answer." });
    render(withProvider(<Message message={completed} />));

    expect(screen.queryByRole("button", { name: /regenerate/i })).not.toBeInTheDocument();
  });

  it("shows Regenerate for a mock-path completed message", () => {
    mockAppState.activeChat = makeChat(); // no backendSessionId
    const completed = makeMessage({ status: "complete", text: "A mock answer." });
    render(withProvider(<Message message={completed} />));

    expect(screen.getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  });
});

describe("Message — ApprovalCard placement (Phase 4G hardening pass — per-message ownership)", () => {
  const pendingAction = {
    proposal_id: "p1",
    operation: "teams.sendMessage",
    status: "pending",
    summary: null,
    title: null,
    members: [],
    chat_id: "c1",
    message: "Hello team",
    expires_at: "2026-01-01T00:05:00Z",
    expires_in_seconds: 300,
    expires_in_minutes: 5,
    target_display_name: null,
  };

  it("renders the card only on the message that owns its actionCards entry", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingAction,
      pendingActionMessageId: "msg-anchor",
      actionCards: { "msg-anchor": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const anchored = makeMessage({ id: "msg-anchor", status: "complete", text: "I can send that." });
    const other = makeMessage({ id: "msg-other", status: "complete", text: "A different message." });

    const { unmount } = render(withProvider(<Message message={anchored} />));
    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
    unmount();

    render(withProvider(<Message message={other} />));
    expect(screen.queryByRole("group", { name: /pending teams action approval/i })).not.toBeInTheDocument();
  });

  it("a NEW proposal on a later message does not move or remove the card on the earlier message — both render, each on its own message", () => {
    const laterAction = { ...pendingAction, proposal_id: "p2" };
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingAction: laterAction,
      pendingActionMessageId: "msg-2",
      actionCards: {
        "msg-1": { proposalId: "p1", pendingAction: { ...pendingAction, status: "rejected" }, approvalCard: undefined, collapsed: true },
        "msg-2": { proposalId: "p2", pendingAction: laterAction, approvalCard: undefined, collapsed: false },
      },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const first = makeMessage({ id: "msg-1", status: "complete", text: "First turn." });
    const second = makeMessage({ id: "msg-2", status: "complete", text: "Second turn." });

    const { unmount } = render(withProvider(<Message message={first} />));
    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
    expect(screen.getByText("Action rejected")).toBeInTheDocument();
    unmount();

    render(withProvider(<Message message={second} />));
    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
    expect(screen.getByText("Action requires approval")).toBeInTheDocument();
  });

  it("never renders more than one card for the same message (no duplicate rendering)", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingAction,
      pendingActionMessageId: "msg-1",
      actionCards: { "msg-1": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const message = makeMessage({ id: "msg-1", status: "complete", text: "I can send that." });

    render(withProvider(<Message message={message} />));

    expect(screen.getAllByRole("group", { name: /pending teams action approval/i })).toHaveLength(1);
  });

  it("never renders the card for a mock/Project/demo message (isBackendMessage false), even if actionCards happened to be set", () => {
    mockAppState.activeChat = makeChat({
      // no backendSessionId — a mock/Project chat
      pendingAction,
      pendingActionMessageId: "msg-1",
      actionCards: { "msg-1": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const message = makeMessage({ id: "msg-1", status: "complete", text: "A mock answer." });

    render(withProvider(<Message message={message} />));

    expect(screen.queryByRole("group", { name: /pending teams action approval/i })).not.toBeInTheDocument();
  });

  it("never renders the card while the message is still pending/streaming, only once complete", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingAction,
      pendingActionMessageId: "msg-1",
      actionCards: { "msg-1": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "I can send" });

    render(withProvider(<Message message={streaming} />));

    expect(screen.queryByRole("group", { name: /pending teams action approval/i })).not.toBeInTheDocument();
  });
});

describe("Message — SelectionCard placement (interaction-capability extension — per-message ownership)", () => {
  const pendingSelection = {
    selection_id: "sel1",
    kind: "teams.chat",
    status: "pending",
    requested_value: "Project Falcon Room",
    options: [{ option_id: "opt1", label: "Project Falcon Room Test" }],
  };

  it("renders the card only on the message that owns its selectionCards entry", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingSelection,
      pendingSelectionMessageId: "msg-anchor",
      selectionCards: {
        "msg-anchor": {
          selectionId: "sel1",
          pendingSelection,
          selectionCard: undefined,
          collapsed: false,
        },
      },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const anchored = makeMessage({ id: "msg-anchor", status: "complete", text: "I couldn't find that exact chat." });
    const other = makeMessage({ id: "msg-other", status: "complete", text: "A different message." });

    const { unmount } = render(withProvider(<Message message={anchored} />));
    expect(screen.getByRole("group", { name: /teams chat selection/i })).toBeInTheDocument();
    unmount();

    render(withProvider(<Message message={other} />));
    expect(screen.queryByRole("group", { name: /teams chat selection/i })).not.toBeInTheDocument();
  });

  it("never renders the card for a mock/Project/demo message (isBackendMessage false), even if selectionCards happened to be set", () => {
    mockAppState.activeChat = makeChat({
      // no backendSessionId — a mock/Project chat
      pendingSelection,
      pendingSelectionMessageId: "msg-1",
      selectionCards: {
        "msg-1": {
          selectionId: "sel1",
          pendingSelection,
          selectionCard: undefined,
          collapsed: false,
        },
      },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const message = makeMessage({ id: "msg-1", status: "complete", text: "A mock answer." });

    render(withProvider(<Message message={message} />));

    expect(screen.queryByRole("group", { name: /teams chat selection/i })).not.toBeInTheDocument();
  });

  it("never renders the card while the message is still pending/streaming, only once complete", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingSelection,
      pendingSelectionMessageId: "msg-1",
      selectionCards: {
        "msg-1": {
          selectionId: "sel1",
          pendingSelection,
          selectionCard: undefined,
          collapsed: false,
        },
      },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "I couldn't find" });

    render(withProvider(<Message message={streaming} />));

    expect(screen.queryByRole("group", { name: /teams chat selection/i })).not.toBeInTheDocument();
  });

  it("a SelectionCard and an ApprovalCard can both render on the SAME message without colliding", () => {
    const pendingAction = {
      proposal_id: "p1",
      operation: "teams.sendMessage",
      status: "pending",
      summary: null,
      title: null,
      members: [],
      chat_id: "c1",
      message: "Hello team",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    };
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingSelection: { ...pendingSelection, status: "resolved" },
      pendingSelectionMessageId: "msg-1",
      selectionCards: {
        "msg-1": {
          selectionId: "sel1",
          pendingSelection,
          selectionCard: { selectionId: "sel1", phase: "resolved", selectedLabel: "Project Falcon Room Test" },
          collapsed: false,
        },
      },
      pendingAction,
      pendingActionMessageId: "msg-1",
      actionCards: { "msg-1": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const message = makeMessage({ id: "msg-1", status: "complete", text: "I've prepared your message." });

    render(withProvider(<Message message={message} />));

    expect(screen.getByRole("group", { name: /teams chat selection/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
  });
});

describe("Message — user message Edit affordance (Phase 4G hardening pass — restored)", () => {
  it("shows the Edit action for a backend-sourced user message — previously hidden entirely, now restored", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const message = makeMessage({ role: "user", status: "complete", text: "send an update" });

    render(withProvider(<Message message={message} />));

    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("still shows the Edit action for a mock-path user message (unaffected)", () => {
    mockAppState.activeChat = makeChat(); // no backendSessionId
    const message = makeMessage({ role: "user", status: "complete", text: "send an update" });

    render(withProvider(<Message message={message} />));

    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("clicking Edit reveals an inline edit box pre-filled with the original text, for a backend-sourced chat too", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const message = makeMessage({ role: "user", status: "complete", text: "send an update" });

    render(withProvider(<Message message={message} />));
    act(() => {
      screen.getByRole("button", { name: "Edit" }).click();
    });

    expect(screen.getByRole("textbox", { name: /edit message/i })).toHaveValue("send an update");
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("Send calls editMessage with the edited text, for a backend-sourced chat", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const message = makeMessage({ id: "msg-user-1", role: "user", status: "complete", text: "original text" });

    render(withProvider(<Message message={message} />));
    act(() => {
      screen.getByRole("button", { name: "Edit" }).click();
    });
    const textarea = screen.getByRole("textbox", { name: /edit message/i });
    fireEvent.change(textarea, { target: { value: "edited text" } });
    act(() => {
      screen.getByRole("button", { name: "Send" }).click();
    });

    expect(mockAppState.editMessage).toHaveBeenCalledExactlyOnceWith("msg-user-1", "edited text");
  });

  it("Cancel discards the edit box without calling editMessage", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const message = makeMessage({ role: "user", status: "complete", text: "original text" });

    render(withProvider(<Message message={message} />));
    act(() => {
      screen.getByRole("button", { name: "Edit" }).click();
    });
    act(() => {
      screen.getByRole("button", { name: "Cancel" }).click();
    });

    expect(mockAppState.editMessage).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: /edit message/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });
});

describe("Message — expandable, sanitized run trace (pre-4H milestone)", () => {
  it("renders the live trace (with expand affordance) once steps have arrived for the run-target message", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: {
        runToken: "r1",
        assistantMessageId: "msg-1",
        currentActivity: { stage: "teams_context", label: "Reviewing the selected Teams conversation" },
        runStartedAt: Date.now(),
      },
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s1", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s2", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s3", category: "evidence", label: "Reviewed 4 retrieved messages", status: "completed" },
          ],
          expanded: false,
        },
      },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getByRole("status")).toHaveTextContent("Reviewing the selected Teams conversation");
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("expands the live trace to show steps-so-far on click, without resetting the elapsed timer", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s1", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s2", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s3", category: "evidence", label: "Reviewed 4 retrieved messages", status: "completed" },
          ],
          expanded: true,
        },
      },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getByText("Used the selected Teams conversation")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(mockAppState.toggleRunTraceExpanded).toHaveBeenCalledExactlyOnceWith("chat-1", "msg-1");
  });

  it("renders 'Worked for Xs' above the response once the run has completed successfully", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s2", category: "response", label: "Generated the response", status: "completed" },
          ],
          expanded: false,
          finalDurationSeconds: 34,
          outcome: "ok",
        },
      },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Here is the summary." });
    render(withProvider(<Message message={completed} />));

    expect(screen.getByText("Worked for 34s")).toBeInTheDocument();
    expect(screen.getByText("Here is the summary.")).toBeInTheDocument();
  });

  it("renders 'Stopped after Xs' — never a success header — for a failed run", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      runTraces: {
        "msg-1": {
          steps: [{ stepId: "s1", category: "response", label: "Request could not be completed", status: "failed" }],
          expanded: false,
          finalDurationSeconds: 12,
          outcome: "error",
        },
      },
    });
    const errored = makeMessage({ id: "msg-1", status: "error", text: "", errorMessage: "Connection lost." });
    render(withProvider(<Message message={errored} />));

    expect(screen.getByText("Stopped after 12s")).toBeInTheDocument();
    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
    // The error notice remains, unreplaced by the trace header.
    expect(screen.getByText("Connection lost.")).toBeInTheDocument();
  });

  it("never renders a completed trace on a message that doesn't own a runTraces record (ownership)", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      runTraces: {
        "msg-owner": {
          steps: [{ stepId: "s1", category: "response", label: "Generated the response", status: "completed" }],
          expanded: false,
          finalDurationSeconds: 5,
          outcome: "ok",
        },
      },
    });
    const other = makeMessage({ id: "msg-other", status: "complete", text: "Unrelated answer." });
    render(withProvider(<Message message={other} />));

    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
  });

  it("never renders a trace for a mock/Project/demo message (isBackendMessage false), even if runTraces happened to be set", () => {
    mockAppState.activeChat = makeChat({
      // no backendSessionId — a mock/Project chat
      runTraces: {
        "msg-1": {
          steps: [{ stepId: "s1", category: "response", label: "Generated the response", status: "completed" }],
          expanded: false,
          finalDurationSeconds: 5,
          outcome: "ok",
        },
      },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Mock answer." });
    render(withProvider(<Message message={completed} />));

    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
  });

  it("renders no trace header at all while the run is still live but no run-owned trace record exists yet (defensive)", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/stopped after/i)).not.toBeInTheDocument();
  });

  it("does not render a completed trace while the run's message is still mid-stream (finalDurationSeconds not yet set)", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
      runTraces: { "msg-1": { steps: [], expanded: false } },
    });
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "The evi" });
    render(withProvider(<Message message={streaming} />));

    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
    expect(screen.getByText("The evi")).toBeInTheDocument();
  });
});

describe("Message — RunTrace remains mounted for the run's entire lifetime (streaming-gap fix)", () => {
  const NOW = new Date("2026-01-01T00:00:00.000Z").getTime();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function chatWithRun(overrides: Partial<Chat> = {}) {
    return makeChat({
      backendSessionId: "s1",
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: NOW },
      runTraces: { "msg-1": { steps: [], expanded: false } },
      ...overrides,
    });
  }

  it("never disappears across pending -> streaming -> complete — always exactly one live/completed trace region visible", () => {
    mockAppState.activeChat = chatWithRun({
      run: {
        runToken: "r1",
        assistantMessageId: "msg-1",
        currentActivity: { stage: "teams_context", label: "Reviewing the selected Teams conversation" },
        runStartedAt: NOW,
      },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={pending} />));
    expect(screen.getByRole("status")).toHaveTextContent("Reviewing the selected Teams conversation");

    // First token arrives: status.clear fires (currentActivity -> null),
    // message.delta flips status to "streaming" -- the trace must still
    // be there, not vanish.
    mockAppState.activeChat = chatWithRun({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: NOW },
    });
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "The" });
    rerender(withProvider(<Message message={streaming} />));
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("The")).toBeInTheDocument();

    // More streaming.
    const streaming2 = makeMessage({ id: "msg-1", status: "streaming", text: "The answer" });
    rerender(withProvider(<Message message={streaming2} />));
    expect(screen.getByRole("status")).toBeInTheDocument();

    // Completion: chat.run clears, the trace record freezes.
    mockAppState.activeChat = chatWithRun({
      run: undefined,
      runTraces: {
        "msg-1": {
          steps: [{ stepId: "s1", category: "response", label: "Generated the response", status: "completed" }],
          expanded: false,
          finalDurationSeconds: 4,
          outcome: "ok",
        },
      },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "The answer is X." });
    rerender(withProvider(<Message message={completed} />));
    expect(screen.getByText("Worked for 4s")).toBeInTheDocument();
    expect(screen.getByText("The answer is X.")).toBeInTheDocument();
  });

  it("uses the neutral 'Working' fallback once status is cleared during streaming, never stale/'Thinking' text", () => {
    mockAppState.activeChat = chatWithRun();
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Partial" });
    render(withProvider(<Message message={streaming} />));

    expect(screen.getByRole("status")).toHaveTextContent("Working");
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/thought/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/reasoning/i)).not.toBeInTheDocument();
  });

  it("still shows 'Thinking…' (not 'Working') before any token/status has arrived — fallback only applies once streaming", () => {
    mockAppState.activeChat = chatWithRun();
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    render(withProvider(<Message message={pending} />));

    expect(screen.getByRole("status")).toHaveTextContent("Thinking…");
  });

  it("the elapsed timer does not reset when the message transitions from pending to streaming", () => {
    mockAppState.activeChat = chatWithRun();
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={pending} />));

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText("· 10s")).toBeInTheDocument();

    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Hello" });
    rerender(withProvider(<Message message={streaming} />));
    // Same runStartedAt, no remount -- still 10s, not reset to 0.
    expect(screen.getByText("· 10s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("· 15s")).toBeInTheDocument();
  });

  it("expansion state survives the pending -> streaming transition without being reset/remounted", () => {
    mockAppState.activeChat = chatWithRun({
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s0", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s0b", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s0c", category: "evidence", label: "Reviewed 2 retrieved messages", status: "completed" },
          ],
          expanded: true,
        },
      },
    });
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={pending} />));
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");

    mockAppState.activeChat = chatWithRun({
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s0", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s2", category: "evidence", label: "Reviewed 2 retrieved messages", status: "completed" },
          ],
          expanded: true,
        },
      },
    });
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Hi" });
    rerender(withProvider(<Message message={streaming} />));

    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Used the selected Teams conversation")).toBeInTheDocument();
  });

  it("newly arriving trace.step-derived steps append and remain visible while expanded during streaming (no duplication)", () => {
    mockAppState.activeChat = chatWithRun({
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s0", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s2", category: "evidence", label: "Reviewed 4 retrieved messages", status: "completed" },
          ],
          expanded: true,
        },
      },
    });
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Hi" });
    const { rerender } = render(withProvider(<Message message={streaming} />));
    expect(screen.getAllByText("Used the selected Teams conversation")).toHaveLength(1);
    expect(screen.queryByText("Generated the response")).not.toBeInTheDocument();

    mockAppState.activeChat = chatWithRun({
      runTraces: {
        "msg-1": {
          steps: [
            { stepId: "s0", category: "case", label: "Loaded active case context", status: "completed" },
            { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
            { stepId: "s2", category: "evidence", label: "Reviewed 4 retrieved messages", status: "completed" },
            { stepId: "s3", category: "response", label: "Generated the response", status: "completed" },
          ],
          expanded: true,
        },
      },
    });
    rerender(withProvider(<Message message={streaming} />));

    expect(screen.getAllByText("Used the selected Teams conversation")).toHaveLength(1);
    expect(screen.getAllByText("Generated the response")).toHaveLength(1);
  });

  it("exactly one trace disclosure element exists at any point in the lifecycle (no duplicate pre/post-stream instances)", () => {
    mockAppState.activeChat = chatWithRun();
    const pending = makeMessage({ id: "msg-1", status: "pending", text: "" });
    const { rerender } = render(withProvider(<Message message={pending} />));
    expect(screen.getAllByRole("status")).toHaveLength(1);

    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Hi" });
    rerender(withProvider(<Message message={streaming} />));
    expect(screen.getAllByRole("status")).toHaveLength(1);

    mockAppState.activeChat = chatWithRun({
      run: undefined,
      runTraces: { "msg-1": { steps: [], expanded: false, finalDurationSeconds: 6, outcome: "ok" } },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Done." });
    rerender(withProvider(<Message message={completed} />));
    // Completed mode renders no role="status" region at all (only the
    // live CurrentActivity composition does) -- and no leftover live one.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("Worked for 6s")).toBeInTheDocument();
  });

  it("terminal error transition: 'Working' becomes 'Stopped after Xs', in the same slot, alongside the error notice", () => {
    mockAppState.activeChat = chatWithRun();
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Partial" });
    const { rerender } = render(withProvider(<Message message={streaming} />));
    expect(screen.getByRole("status")).toHaveTextContent("Working");

    mockAppState.activeChat = chatWithRun({
      run: undefined,
      runTraces: {
        "msg-1": {
          steps: [{ stepId: "s1", category: "response", label: "Request could not be completed", status: "failed" }],
          expanded: false,
          finalDurationSeconds: 7,
          outcome: "error",
        },
      },
    });
    const errored = makeMessage({ id: "msg-1", status: "error", text: "Partial", errorMessage: "Connection lost." });
    rerender(withProvider(<Message message={errored} />));

    expect(screen.getByText("Stopped after 7s")).toBeInTheDocument();
    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
    expect(screen.getByText("Connection lost.")).toBeInTheDocument();
  });
});

describe("Message — structured Teams source/provenance (pre-4H UX/provenance milestone)", () => {
  function makeSource(overrides: Partial<SourceReferenceDTO> = {}): SourceReferenceDTO {
    return {
      source_id: "src1",
      source_type: "teams",
      label: "Teams conversation",
      title: "Ops Bridge",
      message_count: 29,
      period_start: "2026-08-26T09:00:00Z",
      period_end: "2026-09-01T09:00:00Z",
      contributors: ["Alex", "Priya"],
      evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." }],
      ...overrides,
    };
  }

  it("renders the compact Source chip only on the message that owns the source, never the answer body itself", () => {
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      sources: { "msg-1": makeSource() },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Here is the summary." });
    render(withProvider(<Message message={completed} />));

    expect(screen.getByText("Here is the summary.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Source · Teams conversation/ })).toBeInTheDocument();
    // The long provenance footer this replaces must never appear in the
    // answer body — there is no frontend text-stripping involved because
    // the backend never sends that text in the first place; this asserts
    // the rendered message text is exactly the concise answer.
    expect(screen.queryByText(/this information is based on/i)).not.toBeInTheDocument();
  });

  it("clicking Source opens the existing right-side drawer with the safe Teams fields", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-1": makeSource() } });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Here is the summary." });
    render(withProvider(<Message message={completed} />));

    fireEvent.click(screen.getByRole("button", { name: /Source/ }));

    expect(screen.getByText("Microsoft Teams")).toBeInTheDocument();
    expect(screen.getByText("Ops Bridge")).toBeInTheDocument();
    expect(screen.getByText("29")).toBeInTheDocument();
    expect(screen.getByText("Alex, Priya")).toBeInTheDocument();
  });

  it("never renders a raw chat_id anywhere in the chip or drawer", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-1": makeSource() } });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Here is the summary." });
    render(withProvider(<Message message={completed} />));
    fireEvent.click(screen.getByRole("button", { name: /Source/ }));

    expect(document.body.textContent).not.toMatch(/19:[a-f0-9-]+@thread/);
  });

  it("renders the chip only on the OWNING message — a different message with no sources entry shows nothing", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-owner": makeSource() } });
    const other = makeMessage({ id: "msg-other", status: "complete", text: "An unrelated answer." });
    render(withProvider(<Message message={other} />));

    expect(screen.queryByRole("button", { name: /Source/ })).not.toBeInTheDocument();
  });

  it("never renders the chip for a mock/Project/demo message (isBackendMessage false), even if sources happened to be set", () => {
    mockAppState.activeChat = makeChat({
      // no backendSessionId — a mock/Project chat
      sources: { "msg-1": makeSource() },
    });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "A mock answer." });
    render(withProvider(<Message message={completed} />));

    expect(screen.queryByRole("button", { name: /Source/ })).not.toBeInTheDocument();
  });

  it("does not render the chip while the message is still pending/streaming, only once complete", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-1": makeSource() } });
    const streaming = makeMessage({ id: "msg-1", status: "streaming", text: "Partial" });
    render(withProvider(<Message message={streaming} />));

    expect(screen.queryByRole("button", { name: /Source/ })).not.toBeInTheDocument();
  });

  it("a MOP citation and a Teams source chip can both render on the same message without colliding", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-1": makeSource() } });
    const completed = makeMessage({
      id: "msg-1",
      status: "complete",
      text: "Here is the summary.",
      citations: [
        {
          id: "c1",
          docId: "MOP-042",
          docTitle: "Incident Response Runbook",
          version: "v3",
          status: "approved",
          section: "4.2",
          excerpt: "Escalate to on-call within 15 minutes.",
          scope: "global",
          scopeLabel: "Global approved knowledge",
        },
      ],
    });
    render(withProvider(<Message message={completed} />));

    expect(screen.getByRole("button", { name: /MOP-042/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Source · Teams conversation/ })).toBeInTheDocument();
  });
});

describe("Message — Markdown rendering (frontend Markdown pass)", () => {
  function makeSource(overrides: Partial<SourceReferenceDTO> = {}): SourceReferenceDTO {
    return {
      source_id: "src1",
      source_type: "teams",
      label: "Teams conversation",
      title: "Ops Bridge",
      message_count: 29,
      period_start: "2026-08-26T09:00:00Z",
      period_end: "2026-09-01T09:00:00Z",
      contributors: ["Alex", "Priya"],
      evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." }],
      ...overrides,
    };
  }

  it("renders a real backend assistant message's Markdown — bold section labels become <strong>, list items become real <li>, no literal ** reaches the DOM", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const completed = makeMessage({
      id: "msg-1",
      status: "complete",
      text: "**Decisions**\n- TG2 is conditionally approved.\n- Phase 1 is complete.",
    });
    render(withProvider(<Message message={completed} />));

    const strong = screen.getByText("Decisions");
    expect(strong.tagName).toBe("STRONG");
    expect(screen.getByText("TG2 is conditionally approved.").closest("li")).not.toBeNull();
    expect(document.body.textContent).not.toContain("**");
  });

  it("a mock/Project (non-backend) message keeps the existing plain-text/block-tag rendering, completely unaffected by the Markdown pass", () => {
    mockAppState.activeChat = makeChat(); // no backendSessionId — mock/demo chat
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "**Decisions**\n- Item one" });
    render(withProvider(<Message message={completed} />));

    // The mock path never interprets Markdown — the asterisks are shown
    // exactly as typed, in a single plain paragraph, unchanged from
    // before this pass (see MessageBody/parseTextBlocks).
    expect(screen.getByText(/\*\*Decisions\*\*/)).toBeInTheDocument();
    expect(document.querySelector("li")).toBeNull();
  });

  it("Markdown content and the Source chip render together, but the chip stays a sibling — never inside the Markdown DOM/content, and its click behavior is unaffected (section 27/35)", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1", sources: { "msg-1": makeSource() } });
    const completed = makeMessage({
      id: "msg-1",
      status: "complete",
      text: "**Summary**\nEverything is on track.",
    });
    const { container } = render(withProvider(<Message message={completed} />));

    const strong = screen.getByText("Summary");
    expect(strong.tagName).toBe("STRONG");

    const sourceButton = screen.getByRole("button", { name: /Source · Teams conversation/ });
    // The Source chip must not be a descendant of the Markdown container.
    const markdownRoot = container.querySelector('[class*="leading-relaxed"]');
    expect(markdownRoot?.contains(sourceButton)).toBe(false);

    fireEvent.click(sourceButton);
    expect(screen.getByText("Ops Bridge")).toBeInTheDocument();
  });

  it("ApprovalCard still renders independently alongside a Markdown-rendered message body (section 36)", () => {
    const pendingAction = {
      proposal_id: "p1",
      operation: "teams.sendMessage" as const,
      status: "pending" as const,
      summary: null,
      title: null,
      members: [],
      chat_id: "c1",
      message: "Hello team",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    };
    mockAppState.activeChat = makeChat({
      backendSessionId: "s1",
      pendingAction,
      pendingActionMessageId: "msg-1",
      actionCards: { "msg-1": { proposalId: "p1", pendingAction, approvalCard: undefined, collapsed: false } },
    });
    mockAppState.state.chats["chat-1"] = mockAppState.activeChat;
    const completed = makeMessage({
      id: "msg-1",
      status: "complete",
      text: "**Proposal**\nI've prepared a message for the team.",
    });
    render(withProvider(<Message message={completed} />));

    expect(screen.getByText("Proposal").tagName).toBe("STRONG");
    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
  });

  it("a short backend response stays visually simple — a single plain paragraph, no lists/headings/extra hierarchy", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const completed = makeMessage({ id: "msg-1", status: "complete", text: "Hello! How can I help?" });
    const { container } = render(withProvider(<Message message={completed} />));

    expect(screen.getByText("Hello! How can I help?").tagName).toBe("P");
    expect(container.querySelectorAll("h1, h2, h3, ul, ol")).toHaveLength(0);
  });

  it("a long structured backend response preserves every category and item, with real list/bold semantics (section 38)", () => {
    mockAppState.activeChat = makeChat({ backendSessionId: "s1" });
    const text = [
      "Here's an interpretation of the conversation.",
      "",
      "**Decisions**",
      "- TG2 is conditionally approved.",
      "",
      "**Actions**",
      "- Complete the scope.",
      "- Follow up with the team.",
      "",
      "**Risks**",
      "- Geographic data restrictions.",
    ].join("\n");
    const completed = makeMessage({ id: "msg-1", status: "complete", text });
    const { container } = render(withProvider(<Message message={completed} />));

    for (const label of ["Decisions", "Actions", "Risks"]) {
      expect(screen.getByText(label).tagName).toBe("STRONG");
    }
    expect(container.querySelectorAll("li")).toHaveLength(4);
    expect(document.body.textContent).not.toContain("**");
  });
});
