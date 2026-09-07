import { describe, expect, it } from "vitest";
import { initialState, reducer, type AppState } from "./AppState";
import type { SavedSessionSummaryDTO, SessionHistoryResponseDTO } from "../api/types";

function summary(overrides: Partial<SavedSessionSummaryDTO> = {}): SavedSessionSummaryDTO {
  return { session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00", ...overrides };
}

describe("reducer — HYDRATE_SAVED_SESSIONS", () => {
  it("inserts a brand-new hydrated chat with id=backendSessionId=session_id and historyHydrationStatus unloaded", () => {
    const state = reducer(initialState, {
      type: "HYDRATE_SAVED_SESSIONS",
      payload: { sessions: [summary()] },
    });

    const chat = state.chats["s1"];
    expect(chat).toBeDefined();
    expect(chat.id).toBe("s1");
    expect(chat.backendSessionId).toBe("s1");
    expect(chat.title).toBe("B4B Live Persistence Test");
    expect(chat.projectId).toBeNull();
    expect(chat.messageIds).toEqual([]);
    expect(chat.historyHydrationStatus).toBe("unloaded");
    expect(state.chatOrder).toEqual(["s1"]);
    // Boot must never auto-open a conversation.
    expect(state.activeChatId).toBeNull();
  });

  it("preserves backend order (already newest-activity-first) across multiple summaries", () => {
    const state = reducer(initialState, {
      type: "HYDRATE_SAVED_SESSIONS",
      payload: {
        sessions: [summary({ session_id: "newest" }), summary({ session_id: "older" })],
      },
    });
    expect(state.chatOrder).toEqual(["newest", "older"]);
  });

  it("is idempotent — dispatching the same summaries twice never duplicates a chat or chatOrder entry", () => {
    let state = reducer(initialState, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary()] } });
    state = reducer(state, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary()] } });

    expect(state.chatOrder).toEqual(["s1"]);
    expect(Object.keys(state.chats)).toEqual(["s1"]);
  });

  it("dedupes by backendSessionId, not by Chat.id — reconciles title only for an already-existing chat", () => {
    let state: AppState = {
      ...initialState,
      chats: {
        local1: {
          id: "local1",
          title: "Old title",
          projectId: null,
          pinned: false,
          createdAt: 500,
          messageIds: ["m1"],
          activeSkillId: null,
          connectorIds: [],
          selectedModelId: "gemini",
          thinkingEffort: "instant",
          backendSessionId: "s1",
        },
      },
      chatOrder: ["local1"],
      messages: {
        m1: { id: "m1", chatId: "local1", role: "user", text: "hi", status: "complete", createdAt: 500 },
      },
    };

    state = reducer(state, {
      type: "HYDRATE_SAVED_SESSIONS",
      payload: { sessions: [summary({ session_id: "s1", title: "Server title" })] },
    });

    // No duplicate chat inserted, no chatOrder change, local messages untouched.
    expect(Object.keys(state.chats)).toEqual(["local1"]);
    expect(state.chatOrder).toEqual(["local1"]);
    expect(state.chats.local1.title).toBe("Server title");
    expect(state.chats.local1.messageIds).toEqual(["m1"]);
  });

  it("never overwrites an existing chat's active run, local messages, or draft-specific state", () => {
    let state: AppState = {
      ...initialState,
      chats: {
        local1: {
          id: "local1",
          title: "Old title",
          projectId: null,
          pinned: false,
          createdAt: 500,
          messageIds: ["m1", "m2"],
          activeSkillId: "skill-x",
          connectorIds: ["conn-x"],
          selectedModelId: "gemini",
          thinkingEffort: "instant",
          backendSessionId: "s1",
          run: { runToken: "r1", assistantMessageId: "m2", currentActivity: null, runStartedAt: 100 },
        },
      },
      chatOrder: ["local1"],
    };

    state = reducer(state, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary({ session_id: "s1" })] } });

    expect(state.chats.local1.run).toEqual({ runToken: "r1", assistantMessageId: "m2", currentActivity: null, runStartedAt: 100 });
    expect(state.chats.local1.messageIds).toEqual(["m1", "m2"]);
    expect(state.chats.local1.activeSkillId).toBe("skill-x");
  });
});

function hydratedChat(overrides: Partial<AppState["chats"][string]> = {}) {
  return {
    id: "s1",
    title: "B4B Live Persistence Test",
    projectId: null,
    pinned: false,
    createdAt: 1000,
    messageIds: [],
    activeSkillId: null,
    connectorIds: [],
    selectedModelId: "gemini",
    thinkingEffort: "instant" as const,
    backendSessionId: "s1",
    historyHydrationStatus: "unloaded" as const,
    ...overrides,
  };
}

function historyResponse(overrides: Partial<SessionHistoryResponseDTO> = {}): SessionHistoryResponseDTO {
  return {
    session_id: "s1",
    messages: [
      {
        message_id: "e-1:user",
        turn_id: "e-1",
        role: "user",
        text: "B4B live persistence retry. Reply with: B4B smoke confirmed.",
        created_at: "2026-09-07T22:25:09.620902+00:00",
        attachments: [],
      },
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "B4B smoke confirmed.",
        created_at: "2026-09-07T22:25:12.382673+00:00",
        attachments: [],
      },
    ],
    ...overrides,
  };
}

describe("reducer — HISTORY_FETCH_STARTED / SUCCEEDED / FAILED / RETRY_HISTORY_LOAD", () => {
  function seedHydrated(): AppState {
    return { ...initialState, chats: { s1: hydratedChat() }, chatOrder: ["s1"] };
  }

  it("HISTORY_FETCH_STARTED flips status to loading and clears any prior error", () => {
    const state = reducer(
      { ...seedHydrated(), chats: { s1: hydratedChat({ historyHydrationStatus: "error", historyError: "old" }) } },
      { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } },
    );
    expect(state.chats.s1.historyHydrationStatus).toBe("loading");
    expect(state.chats.s1.historyError).toBeUndefined();
  });

  it("HISTORY_FETCH_SUCCEEDED maps messages with exact ids, roles, text, order, and status complete", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    state = reducer(state, { type: "HISTORY_FETCH_SUCCEEDED", payload: { chatId: "s1", response: historyResponse() } });

    expect(state.chats.s1.historyHydrationStatus).toBe("loaded");
    expect(state.chats.s1.messageIds).toEqual(["e-1:user", "e-1:assistant"]);
    expect(state.messages["e-1:user"]).toMatchObject({
      id: "e-1:user",
      chatId: "s1",
      role: "user",
      text: "B4B live persistence retry. Reply with: B4B smoke confirmed.",
      status: "complete",
    });
    expect(state.messages["e-1:assistant"]).toMatchObject({
      id: "e-1:assistant",
      chatId: "s1",
      role: "assistant",
      text: "B4B smoke confirmed.",
      status: "complete",
    });
    expect(state.messages["e-1:user"].createdAt).toBeLessThan(state.messages["e-1:assistant"].createdAt);
  });

  it("HISTORY_FETCH_SUCCEEDED never fabricates citations/sources/action or selection cards/run traces", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    state = reducer(state, { type: "HISTORY_FETCH_SUCCEEDED", payload: { chatId: "s1", response: historyResponse() } });

    expect(state.messages["e-1:assistant"].citations).toBeUndefined();
    expect(state.chats.s1.actionCards).toBeUndefined();
    expect(state.chats.s1.selectionCards).toBeUndefined();
    expect(state.chats.s1.runTraces).toBeUndefined();
    expect(state.chats.s1.sources).toBeUndefined();
  });

  it("a user-only failed turn (no assistant message in the response) renders as user only — no fabricated assistant reply", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    state = reducer(state, {
      type: "HISTORY_FETCH_SUCCEEDED",
      payload: {
        chatId: "s1",
        response: historyResponse({
          messages: [
            {
              message_id: "e-2:user",
              turn_id: "e-2",
              role: "user",
              text: "diagnose the fault",
              created_at: "2026-09-07T21:46:46.130347+00:00",
              attachments: [],
            },
          ],
        }),
      },
    });

    expect(state.chats.s1.messageIds).toEqual(["e-2:user"]);
    expect(state.messages["e-2:user"].role).toBe("user");
  });

  it("ignores a HISTORY_FETCH_SUCCEEDED for a chat that no longer exists (deleted while in flight)", () => {
    const state = reducer(seedHydrated(), {
      type: "HISTORY_FETCH_SUCCEEDED",
      payload: { chatId: "deleted-chat", response: historyResponse() },
    });
    expect(state.chats["deleted-chat"]).toBeUndefined();
  });

  it("ignores a stale HISTORY_FETCH_SUCCEEDED once the chat has moved past 'loading'", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    state = reducer(state, { type: "HISTORY_FETCH_SUCCEEDED", payload: { chatId: "s1", response: historyResponse() } });
    const loadedMessageIds = state.chats.s1.messageIds;

    // A second, stale success for the same chat must not re-apply on top.
    const restaleState = reducer(state, {
      type: "HISTORY_FETCH_SUCCEEDED",
      payload: { chatId: "s1", response: historyResponse({ messages: [] }) },
    });
    expect(restaleState.chats.s1.messageIds).toEqual(loadedMessageIds);
  });

  it("never clobbers messageIds if a real local turn already started before the history response arrived", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    // Simulate a message having been appended locally in the meantime.
    state = {
      ...state,
      chats: { ...state.chats, s1: { ...state.chats.s1, messageIds: ["local-msg-1"] } },
      messages: {
        "local-msg-1": {
          id: "local-msg-1",
          chatId: "s1",
          role: "user",
          text: "new message",
          status: "complete",
          createdAt: 999,
        },
      },
    };
    state = reducer(state, { type: "HISTORY_FETCH_SUCCEEDED", payload: { chatId: "s1", response: historyResponse() } });

    expect(state.chats.s1.messageIds).toEqual(["local-msg-1"]);
    expect(state.chats.s1.historyHydrationStatus).toBe("loaded");
  });

  it("HISTORY_FETCH_FAILED sets status error with the safe message, retryable via RETRY_HISTORY_LOAD", () => {
    let state = seedHydrated();
    state = reducer(state, { type: "HISTORY_FETCH_STARTED", payload: { chatId: "s1" } });
    state = reducer(state, {
      type: "HISTORY_FETCH_FAILED",
      payload: { chatId: "s1", message: "This conversation could not be loaded. Please try again." },
    });
    expect(state.chats.s1.historyHydrationStatus).toBe("error");
    expect(state.chats.s1.historyError).toBe("This conversation could not be loaded. Please try again.");
    // Messages/messageIds untouched by a failure.
    expect(state.chats.s1.messageIds).toEqual([]);

    state = reducer(state, { type: "RETRY_HISTORY_LOAD", payload: { chatId: "s1" } });
    expect(state.chats.s1.historyHydrationStatus).toBe("unloaded");
    expect(state.chats.s1.historyError).toBeUndefined();
  });

  it("RETRY_HISTORY_LOAD is a no-op unless the chat is currently in the error state", () => {
    const state = reducer(seedHydrated(), { type: "RETRY_HISTORY_LOAD", payload: { chatId: "s1" } });
    expect(state.chats.s1.historyHydrationStatus).toBe("unloaded");
  });
});

describe("reducer — RENAME_CHAT (still the local-apply step for a backend-backed rename)", () => {
  it("applies a trimmed title to any chat, regardless of backendSessionId", () => {
    const state = reducer(
      { ...initialState, chats: { s1: hydratedChat() }, chatOrder: ["s1"] },
      { type: "RENAME_CHAT", payload: { chatId: "s1", title: "B4C UI Rename Test" } },
    );
    expect(state.chats.s1.title).toBe("B4C UI Rename Test");
  });
});

describe("reducer — B4C correction pass: global savedChatsHydrationStatus", () => {
  it("initial state starts as loading", () => {
    expect(initialState.savedChatsHydrationStatus).toBe("loading");
    expect(initialState.savedChatsHydrationError).toBeUndefined();
  });

  it("HYDRATE_SAVED_SESSIONS with real sessions settles status to loaded", () => {
    const state = reducer(initialState, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary()] } });
    expect(state.savedChatsHydrationStatus).toBe("loaded");
    expect(state.savedChatsHydrationError).toBeUndefined();
  });

  it("HYDRATE_SAVED_SESSIONS with an EMPTY sessions array is a genuine 'loaded' — never 'loading' or 'error'", () => {
    const state = reducer(initialState, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [] } });
    expect(state.savedChatsHydrationStatus).toBe("loaded");
    expect(state.chats).toEqual({});
    expect(state.chatOrder).toEqual([]);
  });

  it("SAVED_CHATS_HYDRATION_FAILED sets status error with a safe message and never touches chats", () => {
    const seeded: AppState = {
      ...initialState,
      chats: { local1: { ...hydratedChat({ id: "local1", backendSessionId: undefined }) } },
      chatOrder: ["local1"],
      savedChatsHydrationStatus: "loading",
    };
    const state = reducer(seeded, {
      type: "SAVED_CHATS_HYDRATION_FAILED",
      payload: { message: "Couldn't load saved chats." },
    });
    expect(state.savedChatsHydrationStatus).toBe("error");
    expect(state.savedChatsHydrationError).toBe("Couldn't load saved chats.");
    // A failed (re)fetch must never erase existing local chats.
    expect(state.chats).toEqual(seeded.chats);
    expect(state.chatOrder).toEqual(["local1"]);
  });

  it("SAVED_CHATS_HYDRATION_STARTED resets to loading and clears any prior error, for retry", () => {
    const errored: AppState = { ...initialState, savedChatsHydrationStatus: "error", savedChatsHydrationError: "old" };
    const state = reducer(errored, { type: "SAVED_CHATS_HYDRATION_STARTED" });
    expect(state.savedChatsHydrationStatus).toBe("loading");
    expect(state.savedChatsHydrationError).toBeUndefined();
  });

  it("a retry cycle (STARTED -> HYDRATE_SAVED_SESSIONS) never duplicates a chat already hydrated by an earlier successful attempt", () => {
    let state = reducer(initialState, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary()] } });
    expect(Object.keys(state.chats)).toEqual(["s1"]);

    // Simulate a retry that re-fetches the SAME saved-chat list.
    state = reducer(state, { type: "SAVED_CHATS_HYDRATION_STARTED" });
    expect(state.savedChatsHydrationStatus).toBe("loading");
    state = reducer(state, { type: "HYDRATE_SAVED_SESSIONS", payload: { sessions: [summary()] } });

    expect(Object.keys(state.chats)).toEqual(["s1"]);
    expect(state.chatOrder).toEqual(["s1"]);
    expect(state.savedChatsHydrationStatus).toBe("loaded");
  });
});
