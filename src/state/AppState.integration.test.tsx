import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PendingActionDTO, PendingSelectionDTO, SSEEvent } from "../api/types";

const createSession = vi.fn();
const rewindSession = vi.fn();
const cancelRun = vi.fn();
vi.mock("../api/sessions", () => ({
  createSession: (...args: unknown[]) => createSession(...args),
  rewindSession: (...args: unknown[]) => rewindSession(...args),
  cancelRun: (...args: unknown[]) => cancelRun(...args),
}));

const runBackendChat = vi.fn();
vi.mock("../api/runBackendChat", () => ({ runBackendChat: (...args: unknown[]) => runBackendChat(...args) }));

const chooseSelection = vi.fn();
const skipSelection = vi.fn();
vi.mock("../api/selections", () => ({
  chooseSelection: (...args: unknown[]) => chooseSelection(...args),
  skipSelection: (...args: unknown[]) => skipSelection(...args),
}));

import { AppStateProvider, useAppState } from "./AppState";

/** Drives every event through the SAME handler-dispatch machinery
 * runBackendChat.ts itself uses in production, so this test exercises the
 * real reducer end to end (mocking only the network boundary). */
function playEvents(events: Array<{ type: SSEEvent["type"]; data: unknown }>) {
  runBackendChat.mockImplementation(async (_sessionId, _message, handlers) => {
    for (const event of events) {
      switch (event.type) {
        case "run.started":
          handlers.onRunStarted("server-run-1");
          break;
        case "status": {
          const d = event.data as { stage: string; label: string };
          handlers.onStatus(d.stage, d.label);
          break;
        }
        case "status.clear":
          handlers.onStatusClear();
          break;
        case "message.delta":
          handlers.onDelta((event.data as { text: string }).text);
          break;
        case "message.completed": {
          const data = event.data as { content: string; source?: unknown };
          handlers.onCompleted(data.content, data.source);
          break;
        }
        case "action.pending":
          handlers.onActionPending(event.data as PendingActionDTO);
          break;
        case "selection.pending":
          handlers.onSelectionPending(event.data as PendingSelectionDTO);
          break;
        case "error":
          handlers.onError(event.data as { code?: string; message: string });
          break;
        case "run.completed":
          handlers.onRunCompleted((event.data as { outcome: "ok" | "error" }).outcome);
          break;
        case "trace.step":
          handlers.onTraceStep(
            event.data as { step_id: string; category: string; label: string; status: "completed" | "warning" | "failed" },
          );
          break;
      }
    }
  });
}

let latest: ReturnType<typeof useAppState>;
function Harness() {
  latest = useAppState();
  return null;
}

function renderHarness() {
  render(
    <AppStateProvider>
      <Harness />
    </AppStateProvider>,
  );
}

async function sendAndFlush(text: string) {
  await act(async () => {
    latest.setDraftText(text);
  });
  await act(async () => {
    latest.sendMessage();
  });
}

beforeEach(() => {
  createSession.mockReset();
  runBackendChat.mockReset();
  rewindSession.mockReset();
  chooseSelection.mockReset();
  skipSelection.mockReset();
  cancelRun.mockReset();
  createSession.mockImplementation(async () => ({ session_id: `session-${createSession.mock.calls.length}` }));
  rewindSession.mockImplementation(async (sessionId: string) => ({ session_id: sessionId }));
  cancelRun.mockImplementation(async (sessionId: string, runId: string) => ({
    session_id: sessionId,
    run_id: runId,
    cancelled: true,
  }));
});

describe("AppState integration — session lifecycle", () => {
  it("creates a backend session once for a new chat and reuses it for a second send", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "first reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("first message");
    expect(createSession).toHaveBeenCalledOnce();
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].backendSessionId).toBe("session-1");

    await sendAndFlush("second message, same chat");
    expect(createSession).toHaveBeenCalledOnce(); // still just once
    expect(runBackendChat).toHaveBeenLastCalledWith(
      "session-1",
      "second message, same chat",
      expect.anything(),
      expect.anything(),
      expect.anything(),
    );
  });

  it("creates a new session for a different (new) chat", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("chat A message");
    const firstChatId = latest.state.activeChatId!;

    await act(async () => {
      latest.newChat();
    });
    await sendAndFlush("chat B message");
    const secondChatId = latest.state.activeChatId!;

    expect(secondChatId).not.toBe(firstChatId);
    expect(createSession).toHaveBeenCalledTimes(2);
  });
});

describe("AppState integration — dynamic backend-provided status label", () => {
  it("renders the exact backend label verbatim, for a stage this frontend never hardcodes", async () => {
    // "warp_drive_diagnostics" is not a real Stage value and appears
    // nowhere in frontend source — proving the label is displayed as-is,
    // never mapped through a frontend-owned lookup table.
    playEvents([
      { type: "run.started", data: {} },
      { type: "status", data: { stage: "warp_drive_diagnostics", label: "Checking the current fault evidence" } },
    ]);
    renderHarness();

    await act(async () => {
      latest.setDraftText("hello");
    });
    // Don't await sendMessage to completion here — inspect state mid-run.
    act(() => {
      void latest.sendMessage();
    });
    // Allow the queued microtasks (session creation + first handler calls) to flush.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].run?.currentActivity).toEqual({
      stage: "warp_drive_diagnostics",
      label: "Checking the current fault evidence",
    });
  });
});

describe("AppState integration — message.delta accumulation", () => {
  it("accumulates deltas into the assistant message's text via the real reducer", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.delta", data: { text: "The " } },
      { type: "message.delta", data: { text: "evidence " } },
      { type: "message.delta", data: { text: "indicates a config change." } },
      { type: "message.completed", data: { content: "The evidence indicates a config change." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("what happened?");

    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(assistantMessage.text).toBe("The evidence indicates a config change.");
    expect(assistantMessage.status).toBe("complete");
  });
});

describe("AppState integration — action.pending", () => {
  it("populates chat.pendingAction with the exact DTO, with no payload_hash-shaped field", async () => {
    const action: PendingActionDTO = {
      proposal_id: "p1",
      operation: "teams.sendMessage",
      status: "pending",
      summary: "Send an update to the ops channel",
      title: null,
      members: ["alice@example.com"],
      chat_id: "c1",
      message: "Update: resolved.",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I've prepared an update for approval." } },
      { type: "action.pending", data: action },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("send an update");

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].pendingAction).toEqual(action);
    expect(Object.keys(latest.state.chats[chatId].pendingAction ?? {})).not.toContain("payload_hash");
  });

  it("Phase 4G hardening pass: also creates the per-message actionCards record, anchored to the assistant message", async () => {
    const action: PendingActionDTO = {
      proposal_id: "p1",
      operation: "teams.sendMessage",
      status: "pending",
      summary: "Send an update to the ops channel",
      title: null,
      members: ["alice@example.com"],
      chat_id: "c1",
      message: "Update: resolved.",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I've prepared an update for approval." } },
      { type: "action.pending", data: action },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("send an update");

    const chatId = latest.state.activeChatId!;
    const chat = latest.state.chats[chatId];
    const anchorMessageId = chat.pendingActionMessageId!;
    expect(anchorMessageId).toBeTruthy();
    expect(chat.actionCards?.[anchorMessageId]).toEqual({
      proposalId: "p1",
      pendingAction: action,
      approvalCard: undefined,
      collapsed: false,
    });
  });
});

describe("AppState integration — error path", () => {
  it("resolves a transport failure into a safe visible error message on the assistant message", async () => {
    runBackendChat.mockRejectedValue(new Error("ECONNREFUSED raw detail that must never surface"));
    renderHarness();

    await sendAndFlush("hello");

    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(assistantMessage.status).toBe("error");
    expect(assistantMessage.errorMessage).toBeTruthy();
    expect(assistantMessage.errorMessage).not.toContain("ECONNREFUSED");

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].run).toBeUndefined();
  });

  it("resolves a session-creation failure (before any stream ever opened) the same way", async () => {
    createSession.mockRejectedValue(new Error("network down"));
    renderHarness();

    await sendAndFlush("hello");

    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(assistantMessage.status).toBe("error");
    expect(runBackendChat).not.toHaveBeenCalled();
  });
});

describe("AppState integration — editMessage restores backend-chat editing (Phase 4G hardening pass)", () => {
  it("editing a backend-sourced message reuses the SAME session and streams a normal new reply", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "first reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("original prompt");

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].backendSessionId).toBe("session-1");
    const userMessage = latest.activeMessages.find((m) => m.role === "user")!;

    // A different scripted reply for the edit's own new run -- streamed in
    // two deltas, exactly like a normal send, proving streaming is fully
    // intact after an edit (not some degraded/simplified path).
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.delta", data: { text: "Edited " } },
      { type: "message.delta", data: { text: "reply streaming in." } },
      { type: "message.completed", data: { content: "Edited reply streaming in." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.editMessage(userMessage.id, "edited prompt");
    });

    // Same session reused -- never a new one created just for an edit.
    expect(createSession).toHaveBeenCalledOnce();
    // The rewind is requested BEFORE the edited text is ever sent, and
    // targets user-turn index 0 -- there was exactly one user message
    // (the one being edited) before it.
    expect(rewindSession).toHaveBeenCalledExactlyOnceWith("session-1", 0);
    expect(runBackendChat).toHaveBeenLastCalledWith(
      "session-1",
      "edited prompt",
      expect.anything(),
      expect.anything(),
      expect.anything(),
    );

    const editedUserMessage = latest.activeMessages.find((m) => m.role === "user")!;
    expect(editedUserMessage.text).toBe("edited prompt");
    const newAssistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(newAssistantMessage.text).toBe("Edited reply streaming in.");
    expect(newAssistantMessage.status).toBe("complete");
    // The original (pre-edit) assistant message is gone -- exactly one
    // user/assistant pair remains, not both.
    expect(latest.activeMessages.filter((m) => m.role === "assistant")).toHaveLength(1);
  });

  it("a failed rewind leaves the conversation completely untouched -- fail before commit, never a partial truncation", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "first reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("original prompt");

    const chatId = latest.state.activeChatId!;
    const userMessage = latest.activeMessages.find((m) => m.role === "user")!;
    const messageIdsBefore = [...latest.state.chats[chatId].messageIds];
    const messagesBefore = { ...latest.state.messages };

    rewindSession.mockRejectedValueOnce(new Error("simulated rewind failure"));
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    await act(async () => {
      latest.editMessage(userMessage.id, "edited prompt");
    });

    // Nothing about the conversation changed: same message ids, same
    // message content, no new run started, no truncation.
    expect(latest.state.chats[chatId].messageIds).toEqual(messageIdsBefore);
    expect(latest.state.messages[userMessage.id].text).toBe(messagesBefore[userMessage.id].text);
    expect(latest.state.chats[chatId].run).toBeUndefined();
    // The edited text was never sent as a new backend turn.
    expect(runBackendChat).not.toHaveBeenCalledWith("session-1", "edited prompt", expect.anything(), expect.anything());
    expect(alertSpy).toHaveBeenCalledOnce();

    alertSpy.mockRestore();
  });

  it("an edit does not resurrect or duplicate a historical action card unrelated to the edited turn", async () => {
    const proposalDto: PendingActionDTO = {
      proposal_id: "p1",
      operation: "teams.sendMessage",
      status: "pending",
      summary: "prepared",
      title: null,
      members: [],
      chat_id: "c1",
      message: "Hello team",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I've prepared this for you." } },
      { type: "action.pending", data: proposalDto },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message");

    const chatId = latest.state.activeChatId!;
    const firstAssistantMessageId = latest.state.chats[chatId].pendingActionMessageId!;
    expect(latest.state.chats[chatId].actionCards?.[firstAssistantMessageId]).toBeDefined();

    // A second, unrelated turn produces no action.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Sure, here you go." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await sendAndFlush("can you also write code?");
    const secondUserMessage = [...latest.activeMessages].reverse().find((m) => m.role === "user")!;

    // Now edit that SECOND (unrelated) message -- must not touch the
    // first turn's action card at all.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Here's a poem instead." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.editMessage(secondUserMessage.id, "actually write a poem");
    });

    expect(latest.state.chats[chatId].actionCards?.[firstAssistantMessageId]?.proposalId).toBe("p1");
    const allRecords = Object.values(latest.state.chats[chatId].actionCards ?? {});
    expect(allRecords.filter((r) => r.proposalId === "p1")).toHaveLength(1);
  });

  it("the exact U1/A1/U2/A2/U3/A3 scenario: editing U2 discards P1 (owned by A2) while a card on A1 would survive", async () => {
    const proposalDto = (id: string): PendingActionDTO => ({
      proposal_id: id,
      operation: "teams.sendMessage",
      status: "pending",
      summary: "prepared",
      title: null,
      members: [],
      chat_id: "c1",
      message: "Hello team",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: null,
    });

    // U1 -> A1, with its own proposal P0.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "A1" } },
      { type: "action.pending", data: proposalDto("p0") },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("U1");
    const chatId = latest.state.activeChatId!;
    const a1Id = latest.state.chats[chatId].pendingActionMessageId!;

    // U2 -> A2, with its OWN proposal P1 (rejecting P0's card first is not
    // required for a new BACKEND_ACTION_PENDING to attach to a new message).
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "A2" } },
      { type: "action.pending", data: proposalDto("p1") },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await sendAndFlush("U2");
    const a2Id = latest.state.chats[chatId].pendingActionMessageId!;
    expect(a2Id).not.toBe(a1Id);

    // U3 -> A3, no action.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "A3" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await sendAndFlush("U3");

    const u2Message = latest.activeMessages.filter((m) => m.role === "user")[1];

    // Edit U2 -> U2-new.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "A2-new" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.editMessage(u2Message.id, "U2-new");
    });

    // Rewind targeted user-turn index 1 (U2 is the second user message).
    expect(rewindSession).toHaveBeenLastCalledWith("session-1", 1);

    const chat = latest.state.chats[chatId];
    // A2/A3 (and their messages) are gone -- discarded along with U2.
    expect(chat.actionCards?.[a2Id]).toBeUndefined();
    expect(chat.messageIds).not.toContain(a2Id);
    // P0's card, owned by A1 (before the edit point), survives untouched.
    expect(chat.actionCards?.[a1Id]?.proposalId).toBe("p0");
    // The new A2-new has no action card of its own.
    const newAssistant = latest.activeMessages.find((m) => m.role === "assistant" && m.text === "A2-new");
    expect(newAssistant).toBeDefined();
    expect(chat.actionCards?.[newAssistant!.id]).toBeUndefined();
    // Exactly one card remains in the whole chat.
    expect(Object.keys(chat.actionCards ?? {})).toHaveLength(1);
  });
});

// --- Interaction-capability extension: Teams chat-name selection -----------

function selectionDto(overrides: Partial<PendingSelectionDTO> = {}): PendingSelectionDTO {
  return {
    selection_id: "sel1",
    kind: "teams.chat",
    status: "pending",
    requested_value: "Project Falcon Room",
    options: [
      { option_id: "opt1", label: "Project Falcon Room Test" },
      { option_id: "opt2", label: "Project Falcon Test" },
    ],
    ...overrides,
  };
}

describe("AppState integration — selection.pending", () => {
  it("populates chat.pendingSelection and creates the per-message selectionCards record, anchored to the assistant message", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I couldn't find that exact chat, but found similar ones." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();

    await sendAndFlush("send a message to Project Falcon Room saying hi");

    const chatId = latest.state.activeChatId!;
    const chat = latest.state.chats[chatId];
    expect(chat.pendingSelection).toEqual(selectionDto());
    const ownerId = chat.pendingSelectionMessageId!;
    expect(chat.selectionCards?.[ownerId]?.selectionId).toBe("sel1");
  });

  it("action.pending and selection.pending never both populate chat state for the same turn", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "..." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room");

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].pendingAction).toBeUndefined();
  });
});

describe("AppState integration — choosing a WRITE-kind selection", () => {
  it("resolves the card, produces a normal ActionProposal (never executes/sends anything itself), and never re-sends the turn", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I couldn't find that exact chat, but found similar ones." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room saying hi");

    const chatId = latest.state.activeChatId!;
    const ownerId = latest.state.chats[chatId].pendingSelectionMessageId!;

    const newAction: PendingActionDTO = {
      proposal_id: "p-new",
      operation: "teams.sendMessage",
      status: "pending",
      summary: "Send hi to Project Falcon Room Test",
      title: null,
      members: [],
      chat_id: "chat-real-1",
      message: "hi",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: "Project Falcon Room Test",
    };
    chooseSelection.mockResolvedValue({
      session_id: "session-1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: newAction,
    });
    const callsBeforeChoose = runBackendChat.mock.calls.length;

    await act(async () => {
      await latest.chooseSelectionOption(chatId, "sel1", "opt1");
    });

    expect(chooseSelection).toHaveBeenCalledWith("session-1", "sel1", "opt1");
    const chat = latest.state.chats[chatId];
    expect(chat.pendingAction).toEqual(newAction);
    expect(chat.pendingActionMessageId).toBe(ownerId);
    expect(chat.actionCards?.[ownerId]?.proposalId).toBe("p-new");
    expect(chat.selectionCards?.[ownerId]?.selectionCard).toEqual({
      selectionId: "sel1",
      phase: "resolved",
      selectedLabel: "Project Falcon Room Test",
    });
    expect(chat.pendingSelection).toBeNull();
    // Choosing is not approval — nothing was sent, and no new backend turn
    // was triggered for a write-kind resolution (the deterministic
    // completion needs no live model call at all).
    expect(runBackendChat.mock.calls.length).toBe(callsBeforeChoose);
  });

  it("a stale click (selectionId no longer current) never calls the API", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "..." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room");

    const chatId = latest.state.activeChatId!;
    await act(async () => {
      await latest.chooseSelectionOption(chatId, "some-other-selection-id", "opt1");
    });

    expect(chooseSelection).not.toHaveBeenCalled();
  });
});

describe("AppState integration — choosing a READ-kind selection resumes via the backend's own resume_message, never a text replay", () => {
  it("drives a new backend turn with resume_message verbatim, creates NO new user message, and the resumed answer lands in a new assistant message", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I couldn't find that exact chat, but found similar ones." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("summarize Project Falcon Room");

    const chatId = latest.state.activeChatId!;
    // The backend's own deterministic resume text -- destination-free,
    // never the user's original wording (which still said "Project
    // Falcon Room", the OLD unresolved name).
    chooseSelection.mockResolvedValue({
      session_id: "session-1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: null, // READ-kind: no ActionProposal.
      resume_message: "Please provide a summary of the currently selected chat.",
    });
    // Prepare the SECOND (resumed) turn's events.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Here is the summary." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);

    const userMessagesBefore = latest.activeMessages.filter((m) => m.role === "user").length;

    await act(async () => {
      await latest.chooseSelectionOption(chatId, "sel1", "opt1");
    });

    // The driving text is the backend's resume_message, on the SAME
    // session -- never the original ambiguous request text.
    expect(runBackendChat).toHaveBeenLastCalledWith(
      "session-1",
      "Please provide a summary of the currently selected chat.",
      expect.anything(),
      expect.anything(),
      expect.anything(),
    );
    const runBackendChatCall = (runBackendChat as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(runBackendChatCall[1]).not.toContain("Project Falcon Room");

    const chat = latest.state.chats[chatId];
    expect(chat.pendingAction).toBeUndefined(); // still no proposal — this was a read

    // NO new user-visible message was created for the resume.
    const userMessagesAfter = latest.activeMessages.filter((m) => m.role === "user");
    expect(userMessagesAfter).toHaveLength(userMessagesBefore);

    // The resumed answer landed in a brand-new assistant message.
    const assistantMessages = latest.activeMessages.filter((m) => m.role === "assistant");
    const resumedAnswer = assistantMessages.find((m) => m.text === "Here is the summary.");
    expect(resumedAnswer).toBeDefined();
    expect(resumedAnswer!.status).toBe("complete");
  });

  it("never resumes when resume_message is absent (defensive: a write-kind response with pending_action carries no resume_message)", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "..." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room saying hi");

    const chatId = latest.state.activeChatId!;
    const callsBeforeChoose = runBackendChat.mock.calls.length;
    chooseSelection.mockResolvedValue({
      session_id: "session-1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: {
        proposal_id: "p-new",
        operation: "teams.sendMessage",
        status: "pending",
        summary: null,
        title: null,
        members: [],
        chat_id: "chat-real-1",
        message: "hi",
        expires_at: "2026-01-01T00:05:00Z",
        expires_in_seconds: 300,
        expires_in_minutes: 5,
        target_display_name: "Project Falcon Room Test",
      },
      resume_message: null,
    });

    await act(async () => {
      await latest.chooseSelectionOption(chatId, "sel1", "opt1");
    });

    expect(runBackendChat.mock.calls.length).toBe(callsBeforeChoose);
  });

  // Exact regression scenario requested (pre-4H refinement, "MOST
  // IMPORTANT" fix): an ambiguous summary request, resolved by picking a
  // candidate, must complete the ORIGINAL summary automatically — never
  // a second SelectionCard, never a re-ask for the chat name. Synthetic
  // fixture names only (never a real live chat name).
  it("ambiguous summary request -> candidate selected -> summary completes automatically -> no second SelectionCard -> no clarification request", async () => {
    playEvents([
      { type: "run.started", data: {} },
      {
        type: "message.completed",
        data: { content: "I couldn't find an exact match, but found some similar conversations." },
      },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("summarize Project Falcon Room");

    const chatId = latest.state.activeChatId!;
    const firstAssistant = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(Object.keys(latest.state.chats[chatId].selectionCards ?? {})).toHaveLength(1);

    chooseSelection.mockResolvedValue({
      session_id: "session-1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: null, // a read, never a write/proposal
      resume_message: "Please provide a summary of the currently selected chat.",
    });
    playEvents([
      { type: "run.started", data: {} },
      {
        type: "message.completed",
        data: { content: "Here is the summary of Project Falcon Room Test: ..." },
      },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);

    await act(async () => {
      await latest.chooseSelectionOption(chatId, "sel1", "opt1");
    });

    // The resumed turn used the backend's own destination-free resume
    // text -- never the original ambiguous request, never a re-derived
    // chat-name lookup driven by the frontend.
    expect(runBackendChat).toHaveBeenLastCalledWith(
      "session-1",
      "Please provide a summary of the currently selected chat.",
      expect.anything(),
      expect.anything(),
      expect.anything(),
    );

    // No second SelectionCard was ever created -- exactly the one from
    // the original ambiguity, now resolved.
    const chat = latest.state.chats[chatId];
    expect(Object.keys(chat.selectionCards ?? {})).toHaveLength(1);
    expect(chat.selectionCards?.[firstAssistant.id]?.selectionCard?.phase).toBe("resolved");
    expect(chat.pendingSelection).toBeNull(); // no longer an open, actionable ambiguity

    // No new user-visible clarification-request message was created --
    // the resumed answer lands directly in a brand-new assistant message,
    // and no assistant message anywhere asks for the chat name again.
    const assistantTexts = latest.activeMessages.filter((m) => m.role === "assistant").map((m) => m.text);
    expect(assistantTexts.some((t) => t.includes("Here is the summary"))).toBe(true);
    expect(assistantTexts.some((t) => /which chat|what chat|chat name/i.test(t))).toBe(false);

    // Exactly one new assistant message was added for the resume (no
    // duplicate/second answer either).
    const resumedMessages = latest.activeMessages.filter((m) => m.text.includes("Here is the summary"));
    expect(resumedMessages).toHaveLength(1);
  });

  // Exact live-bug regression (pre-4H refinement, item 3/8): a chat was
  // ALREADY selected/used from earlier, unrelated work, then the user
  // explicitly asks about a DIFFERENT, inexact chat. Only synthetic
  // fixture names are used.
  it("a previously-selected chat does not prevent the newly-selected candidate from resuming its own summarize request", async () => {
    // Turn 1: an earlier, completed, unrelated exchange that leaves
    // "Chat A" as the session's current context (mirrors a prior Teams
    // send/read having already resolved it).
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Done — I've sent that message to Chat A." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send hi to Chat A");
    const chatId = latest.state.activeChatId!;

    // Turn 2: "now i need you to sum up this chat room Knowledge
    // Management Daily" -- a DIFFERENT, inexact chat. No exact match ->
    // a fresh SelectionCard for Chat B candidates.
    playEvents([
      { type: "run.started", data: {} },
      {
        type: "message.completed",
        data: { content: "I couldn't find an exact match, but found some similar conversations." },
      },
      {
        type: "selection.pending",
        data: selectionDto({
          selection_id: "sel-b",
          requested_value: "Knowledge Management Daily",
          options: [
            { option_id: "opt-b1", label: "Knowledge Management Daily Sync up" },
            { option_id: "opt-b2", label: "Knowledge Management Weekly" },
          ],
        }),
      },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.setDraftText("now i need you to sum up this chat room Knowledge Management Daily");
    });
    await act(async () => {
      latest.sendMessage();
    });

    const secondAssistant = latest.activeMessages.filter((m) => m.role === "assistant")[1];
    expect(Object.keys(latest.state.chats[chatId].selectionCards ?? {})).toHaveLength(1);

    // User selects "Knowledge Management Daily Sync up" -- the backend's
    // own destination-free resume text drives the next turn, never the
    // stale "Knowledge Management Daily" name, and never "Chat A".
    chooseSelection.mockResolvedValue({
      session_id: "session-1",
      selection_id: "sel-b",
      status: "resolved",
      selected_label: "Knowledge Management Daily Sync up",
      pending_action: null, // a read, not a write -- no ApprovalCard
      resume_message: "Please provide a summary of the currently selected chat.",
    });
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Here is the summary of the daily sync-up notes." } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);

    await act(async () => {
      await latest.chooseSelectionOption(chatId, "sel-b", "opt-b1");
    });

    // No re-ask for the chat name, no replay of the old ambiguous
    // destination or "Chat A".
    const resumeCallText = (runBackendChat as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1];
    expect(resumeCallText).not.toContain("Knowledge Management Daily");
    expect(resumeCallText).not.toContain("Chat A");

    const chat = latest.state.chats[chatId];
    // No second SelectionCard -- exactly the one for Chat B, now resolved.
    expect(Object.keys(chat.selectionCards ?? {})).toHaveLength(1);
    expect(chat.selectionCards?.[secondAssistant.id]?.selectionCard?.phase).toBe("resolved");
    expect(chat.pendingSelection).toBeNull();
    // No ApprovalCard was created — this is a read.
    expect(Object.keys(chat.actionCards ?? {})).toHaveLength(0);

    const assistantTexts = latest.activeMessages.filter((m) => m.role === "assistant").map((m) => m.text);
    expect(assistantTexts.some((t) => t.includes("Here is the summary of the daily sync-up notes"))).toBe(true);
    expect(assistantTexts.some((t) => /which chat|what chat|chat name|need to know/i.test(t))).toBe(false);
  });
});

describe("AppState integration — skipping a selection", () => {
  it("skips without creating any proposal, and the selection is no longer current", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "..." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room saying hi");

    const chatId = latest.state.activeChatId!;
    const ownerId = latest.state.chats[chatId].pendingSelectionMessageId!;
    skipSelection.mockResolvedValue({ session_id: "session-1", selection_id: "sel1", status: "skipped" });

    await act(async () => {
      await latest.skipSelectionOption(chatId, "sel1");
    });

    expect(skipSelection).toHaveBeenCalledWith("session-1", "sel1");
    const chat = latest.state.chats[chatId];
    expect(chat.selectionCards?.[ownerId]?.selectionCard).toEqual({ selectionId: "sel1", phase: "skipped" });
    expect(chat.pendingSelection).toBeNull();
    expect(chat.pendingAction).toBeUndefined();
  });
});

describe("AppState integration — manual correction supersedes a still-open selection card", () => {
  it("typing an exact alternative chat name in a follow-up turn leaves the old card frozen/non-current, without a new selection.pending event", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I couldn't find that exact chat, but found similar ones." } },
      { type: "selection.pending", data: selectionDto() },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("send a message to Project Falcon Room saying hi");

    const chatId = latest.state.activeChatId!;
    const ownerId = latest.state.chats[chatId].pendingSelectionMessageId!;
    expect(latest.state.chats[chatId].pendingSelection).toEqual(selectionDto());

    // The user, instead of clicking a card option, types the exact
    // correct name directly. The backend resolves it exactly (MATCHED)
    // and -- per supersede_active_selection -- does NOT re-emit a
    // selection.pending event for the now-superseded selection_id; it
    // proceeds straight to a normal ActionProposal for the write.
    const newAction: PendingActionDTO = {
      proposal_id: "p-manual",
      operation: "teams.sendMessage",
      status: "pending",
      summary: "Send hi to Project Falcon Room Test",
      title: null,
      members: [],
      chat_id: "chat-real-1",
      message: "hi",
      expires_at: "2026-01-01T00:05:00Z",
      expires_in_seconds: 300,
      expires_in_minutes: 5,
      target_display_name: "Project Falcon Room Test",
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "I've prepared your message." } },
      { type: "action.pending", data: newAction },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await sendAndFlush("actually, send it to Project Falcon Room Test saying hi");

    const chat = latest.state.chats[chatId];
    // The manual correction's own ActionProposal is live and current.
    expect(chat.pendingAction).toEqual(newAction);
    // The OLD selection card is no longer the chat's current selection —
    // it is frozen, historical state; deriveSelectionCardView renders it
    // "stale" rather than a forever-clickable pending card.
    expect(chat.pendingSelection).toBeNull();
    expect(chat.selectionCards?.[ownerId]?.selectionId).toBe("sel1");
    expect(chat.selectionCards?.[ownerId]?.pendingSelection.status).toBe("pending"); // frozen snapshot, unedited
    // No duplicate/second selection card was created for this turn.
    expect(Object.keys(chat.selectionCards ?? {})).toEqual([ownerId]);
  });
});

// --- Expandable, sanitized run trace (pre-4H milestone) ---------------------

describe("AppState integration — expandable, sanitized run trace", () => {
  it("a simple conversation accumulates real trace.step events onto the owning assistant message, frozen at completion", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Hello! How can I help?" } },
      { type: "trace.step", data: { step_id: "s1", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("hello");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    const traceRecord = latest.state.chats[chatId].runTraces?.[assistantMessage.id];

    expect(traceRecord?.steps.map((s) => s.label)).toEqual(["Generated the response"]);
    expect(traceRecord?.outcome).toBe("ok");
    expect(typeof traceRecord?.finalDurationSeconds).toBe("number");
    expect(traceRecord?.expanded).toBe(false); // starts collapsed
  });

  it("live trace steps stream in during the run and remain in the completed trace afterward, in order", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "status", data: { stage: "teams_context", label: "Reviewing the selected Teams conversation" } },
      { type: "trace.step", data: { step_id: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" } },
      {
        type: "trace.step",
        data: { step_id: "s2", category: "evidence", label: "Reviewed 4 retrieved messages", status: "completed", safe_metadata: { message_count: 4 } },
      },
      { type: "status.clear", data: {} },
      { type: "message.completed", data: { content: "Summary of the conversation." } },
      { type: "trace.step", data: { step_id: "s3", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("summarize the selected Teams conversation");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    const traceRecord = latest.state.chats[chatId].runTraces?.[assistantMessage.id];

    // UI PRESENTATION CORRECTION (post-Phase-2): the run's own live
    // status ("Reviewing the selected Teams conversation") is folded in
    // ahead of the genuine trace.step milestones at completion — see
    // AppState.tsx's mergeActivityTrailIntoRunTraceSteps.
    expect(traceRecord?.steps.map((s) => s.label)).toEqual([
      "Reviewing the selected Teams conversation",
      "Used the selected Teams conversation",
      "Reviewed 4 retrieved messages",
      "Generated the response",
    ]);
    expect(traceRecord?.steps[2].safeMetadata).toEqual({ message_count: 4 });
  });

  it("an error run freezes outcome 'error' and never a false 'ok' — the failure step is preserved", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "trace.step", data: { step_id: "s1", category: "response", label: "Request could not be completed", status: "failed" } },
      { type: "error", data: { code: "run_failure", message: "The assistant could not complete this request." } },
      { type: "run.completed", data: { outcome: "error" } },
    ]);
    renderHarness();
    await sendAndFlush("hello");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    const traceRecord = latest.state.chats[chatId].runTraces?.[assistantMessage.id];

    expect(traceRecord?.outcome).toBe("error");
    expect(traceRecord?.steps).toEqual([
      { stepId: "s1", category: "response", label: "Request could not be completed", status: "failed", safeMetadata: undefined },
    ]);
  });

  it("toggling expand/collapse is purely local and never triggers a new network call", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Hi." } },
      { type: "trace.step", data: { step_id: "s1", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("hi");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    const callsBefore = runBackendChat.mock.calls.length;

    await act(async () => {
      latest.toggleRunTraceExpanded(chatId, assistantMessage.id);
    });

    expect(latest.state.chats[chatId].runTraces?.[assistantMessage.id].expanded).toBe(true);
    expect(runBackendChat).toHaveBeenCalledTimes(callsBefore); // no new call
  });

  it("multiple sends in the same chat each get their own distinct, correctly-owned trace", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "first reply" } },
      { type: "trace.step", data: { step_id: "s1", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("first message");

    const chatId = latest.state.activeChatId!;
    const firstAssistant = latest.activeMessages.find((m) => m.role === "assistant")!;

    playEvents([
      { type: "run.started", data: {} },
      {
        type: "trace.step",
        data: { step_id: "s2", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
      },
      { type: "message.completed", data: { content: "second reply" } },
      { type: "trace.step", data: { step_id: "s3", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await sendAndFlush("second message");

    const secondAssistant = latest.activeMessages.filter((m) => m.role === "assistant")[1];
    const chat = latest.state.chats[chatId];

    expect(chat.runTraces?.[firstAssistant.id].steps.map((s) => s.label)).toEqual(["Generated the response"]);
    expect(chat.runTraces?.[secondAssistant.id].steps.map((s) => s.label)).toEqual([
      "Used the selected Teams conversation",
      "Generated the response",
    ]);
  });

  it("editing an earlier message discards the run trace of every turn after it (never replayed)", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "first reply" } },
      { type: "trace.step", data: { step_id: "s1", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("original prompt");

    const chatId = latest.state.activeChatId!;
    const userMessage = latest.activeMessages.find((m) => m.role === "user")!;
    const originalAssistant = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(latest.state.chats[chatId].runTraces?.[originalAssistant.id]).toBeDefined();

    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "edited reply" } },
      { type: "trace.step", data: { step_id: "s2", category: "response", label: "Generated the response", status: "completed" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.editMessage(userMessage.id, "edited prompt");
    });

    // The discarded original assistant message's trace is gone entirely.
    expect(latest.state.chats[chatId].runTraces?.[originalAssistant.id]).toBeUndefined();
    // The new assistant message (from the edit's own run) has its own trace.
    const newAssistant = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(latest.state.chats[chatId].runTraces?.[newAssistant.id]?.steps.map((s) => s.label)).toEqual([
      "Generated the response",
    ]);
  });
});

// --- Stop control (pre-4H refinement) ---------------------------------------

describe("AppState integration — Stop control", () => {
  it("aborts the active run's own client transport signal", async () => {
    let capturedSignal: AbortSignal | undefined;
    runBackendChat.mockImplementation((_sessionId: string, _message: string, _handlers: unknown, signal: AbortSignal) => {
      capturedSignal = signal;
      return new Promise(() => {}); // never resolves -- simulates a genuinely in-flight stream
    });
    renderHarness();
    await sendAndFlush("hello");

    const chatId = latest.state.activeChatId!;
    expect(capturedSignal?.aborted).toBe(false);

    await act(async () => {
      latest.stopActiveRun(chatId);
    });

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("also calls the backend's real cancellation endpoint once the server run_id is known", async () => {
    let handlersRef: { onRunStarted: (id: string) => void } | undefined;
    runBackendChat.mockImplementation((_s: string, _m: string, handlers: { onRunStarted: (id: string) => void }) => {
      handlersRef = handlers;
      return new Promise(() => {});
    });
    renderHarness();
    await sendAndFlush("hello");
    const chatId = latest.state.activeChatId!;

    await act(async () => {
      handlersRef!.onRunStarted("server-run-42");
    });
    expect(latest.state.chats[chatId].run?.serverRunId).toBe("server-run-42");

    await act(async () => {
      latest.stopActiveRun(chatId);
    });

    expect(cancelRun).toHaveBeenCalledExactlyOnceWith("session-1", "server-run-42");
  });

  it("does not call the backend cancel endpoint when no server run_id is known yet", async () => {
    runBackendChat.mockImplementation(() => new Promise(() => {})); // never emits run.started
    renderHarness();
    await sendAndFlush("hello");
    const chatId = latest.state.activeChatId!;

    await act(async () => {
      latest.stopActiveRun(chatId);
    });

    expect(cancelRun).not.toHaveBeenCalled();
    // The frontend still stops immediately regardless.
    expect(latest.state.chats[chatId].run).toBeUndefined();
  });

  it("a failed backend cancel call never changes the already-stopped local UI state", async () => {
    let handlersRef: { onRunStarted: (id: string) => void } | undefined;
    runBackendChat.mockImplementation((_s: string, _m: string, handlers: { onRunStarted: (id: string) => void }) => {
      handlersRef = handlers;
      return new Promise(() => {});
    });
    cancelRun.mockRejectedValue(new Error("network error"));
    renderHarness();
    await sendAndFlush("hello");
    const chatId = latest.state.activeChatId!;
    await act(async () => {
      handlersRef!.onRunStarted("server-run-1");
    });

    await act(async () => {
      latest.stopActiveRun(chatId);
    });
    // Let the rejected promise's .catch() settle without throwing.
    await act(async () => {
      await Promise.resolve();
    });

    expect(latest.state.chats[chatId].run).toBeUndefined();
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(latest.state.chats[chatId].runTraces?.[assistantMessage.id]?.outcome).toBe("stopped");
  });

  it("leaves no hanging chat.run/activity state — the message reaches a terminal, non-pending status", async () => {
    runBackendChat.mockImplementation(() => new Promise(() => {}));
    renderHarness();
    await sendAndFlush("hello");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;
    expect(latest.state.chats[chatId].run).toBeDefined();
    expect(latest.state.messages[assistantMessage.id].status).toBe("pending");

    await act(async () => {
      latest.stopActiveRun(chatId);
    });

    expect(latest.state.chats[chatId].run).toBeUndefined();
    expect(latest.state.messages[assistantMessage.id].status).toBe("complete");
    const traceRecord = latest.state.chats[chatId].runTraces?.[assistantMessage.id];
    expect(traceRecord?.outcome).toBe("stopped");
    expect(typeof traceRecord?.finalDurationSeconds).toBe("number");
  });

  it("preserves any partial streamed text instead of discarding it as an error", async () => {
    let handlersRef: { onDelta: (t: string) => void } | undefined;
    runBackendChat.mockImplementation((_s: string, _m: string, handlers: { onDelta: (t: string) => void }) => {
      handlersRef = handlers;
      return new Promise(() => {});
    });
    renderHarness();
    await sendAndFlush("hello");
    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;

    await act(async () => {
      handlersRef!.onDelta("The partial answer so far");
    });
    expect(latest.state.messages[assistantMessage.id].text).toBe("The partial answer so far");

    await act(async () => {
      latest.stopActiveRun(chatId);
    });

    expect(latest.state.messages[assistantMessage.id].text).toBe("The partial answer so far");
    expect(latest.state.messages[assistantMessage.id].status).toBe("complete");
    expect(latest.state.messages[assistantMessage.id].errorMessage).toBeUndefined();
  });

  it("late events from the stopped run never mutate the conversation (runToken ownership guard)", async () => {
    let handlersRef: { onDelta: (t: string) => void } | undefined;
    runBackendChat.mockImplementation((_s: string, _m: string, handlers: { onDelta: (t: string) => void }) => {
      handlersRef = handlers;
      return new Promise(() => {});
    });
    renderHarness();
    await sendAndFlush("hello");
    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;

    await act(async () => {
      latest.stopActiveRun(chatId);
    });
    expect(latest.state.chats[chatId].run).toBeUndefined();

    // A race: a late delta arrives after the stop was already processed.
    await act(async () => {
      handlersRef!.onDelta("late text that should never appear");
    });

    expect(latest.state.messages[assistantMessage.id].text).not.toContain("late text");
    expect(latest.state.chats[chatId].run).toBeUndefined();
  });

  it("a subsequent message can be sent normally after a stop, reusing the same session", async () => {
    runBackendChat.mockImplementation(() => new Promise(() => {}));
    renderHarness();
    await sendAndFlush("first message");
    const chatId = latest.state.activeChatId!;

    await act(async () => {
      latest.stopActiveRun(chatId);
    });
    expect(latest.state.chats[chatId].run).toBeUndefined();

    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "second reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.setDraftText("second message");
    });
    await act(async () => {
      latest.sendMessage();
    });

    expect(createSession).toHaveBeenCalledOnce(); // same session reused, never a new one
    const secondAssistant = latest.activeMessages.filter((m) => m.role === "assistant")[1];
    expect(secondAssistant.text).toBe("second reply");
    expect(secondAssistant.status).toBe("complete");
  });

  it("is a no-op when there is no active run to stop", async () => {
    renderHarness();
    await act(async () => {
      latest.setDraftText("hello");
    });
    const stateBefore = latest.state;
    await act(async () => {
      latest.stopActiveRun("no-such-chat");
    });
    expect(latest.state).toBe(stateBefore);
  });
});

// --- Structured Teams source/provenance UX (pre-4H) -------------------------

describe("AppState integration — structured Teams source/provenance", () => {
  it("exact scenario: selected Teams chat, 29 messages retrieved, several contributors -> source attached to the owning message only", async () => {
    const source = {
      source_id: "src1",
      source_type: "teams" as const,
      label: "Teams conversation",
      title: "Ops Bridge",
      message_count: 29,
      period_start: "2026-08-26T09:00:00Z",
      period_end: "2026-09-01T09:00:00Z",
      contributors: ["Alex", "Priya", "Sam"],
      evidence: [
        { author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." },
        { author: "Priya", sent_at: "2026-08-27T10:00:00Z", snippet: "Agreed, paging on-call." },
      ],
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Here is the summary of Ops Bridge.", source } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("summarize Ops Bridge");

    const chatId = latest.state.activeChatId!;
    const assistantMessage = latest.activeMessages.find((m) => m.role === "assistant")!;

    // Main answer text does not itself carry the source object (the
    // structured source is a separate, owned artifact — never parsed
    // out of the answer text).
    expect(assistantMessage.text).toBe("Here is the summary of Ops Bridge.");

    const owned = latest.state.chats[chatId].sources?.[assistantMessage.id];
    expect(owned).toEqual(source);
    // Owned by exactly that message — no other message has a source.
    expect(Object.keys(latest.state.chats[chatId].sources ?? {})).toEqual([assistantMessage.id]);
  });

  it("a second, unrelated turn does not move or duplicate the first turn's source", async () => {
    const firstSource = {
      source_id: "src-a",
      source_type: "teams" as const,
      label: "Teams conversation",
      title: "Ops Bridge",
      message_count: 10,
      period_start: null,
      period_end: null,
      contributors: [],
      evidence: [],
    };
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "First summary.", source: firstSource } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("summarize Ops Bridge");
    const chatId = latest.state.activeChatId!;
    const firstAssistant = latest.activeMessages.find((m) => m.role === "assistant")!;

    // Second turn: a plain follow-up with no grounded evidence at all.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Sure, anything else?" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.setDraftText("thanks");
    });
    await act(async () => {
      latest.sendMessage();
    });

    const chat = latest.state.chats[chatId];
    expect(chat.sources?.[firstAssistant.id]).toEqual(firstSource);
    // No source was fabricated for the second, evidence-free turn.
    expect(Object.keys(chat.sources ?? {})).toEqual([firstAssistant.id]);
  });

  it("no source is attached for a run that never retrieved Teams evidence (e.g. plain hello)", async () => {
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "Hello! How can I help?" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    renderHarness();
    await sendAndFlush("hello");

    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].sources).toBeUndefined();
  });
});
