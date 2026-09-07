import { StrictMode } from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SSEEvent } from "../api/types";

const createSession = vi.fn();
const rewindSession = vi.fn();
const cancelRun = vi.fn();
const listSavedSessions = vi.fn();
const getSessionHistory = vi.fn();
const renameSession = vi.fn();
vi.mock("../api/sessions", () => ({
  createSession: (...args: unknown[]) => createSession(...args),
  rewindSession: (...args: unknown[]) => rewindSession(...args),
  cancelRun: (...args: unknown[]) => cancelRun(...args),
  listSavedSessions: (...args: unknown[]) => listSavedSessions(...args),
  getSessionHistory: (...args: unknown[]) => getSessionHistory(...args),
  renameSession: (...args: unknown[]) => renameSession(...args),
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
import { ApiError } from "../api/client";

function playEvents(events: Array<{ type: SSEEvent["type"]; data: unknown }>) {
  runBackendChat.mockImplementation(async (_sessionId, _message, handlers) => {
    for (const event of events) {
      switch (event.type) {
        case "run.started":
          handlers.onRunStarted("server-run-1");
          break;
        case "message.completed":
          handlers.onCompleted((event.data as { content: string }).content);
          break;
        case "run.completed":
          handlers.onRunCompleted((event.data as { outcome: "ok" | "error" }).outcome);
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

beforeEach(() => {
  createSession.mockReset();
  runBackendChat.mockReset();
  rewindSession.mockReset();
  chooseSelection.mockReset();
  skipSelection.mockReset();
  cancelRun.mockReset();
  listSavedSessions.mockReset();
  getSessionHistory.mockReset();
  renameSession.mockReset();

  createSession.mockImplementation(async () => ({ session_id: `session-${createSession.mock.calls.length}` }));
  rewindSession.mockImplementation(async (sessionId: string) => ({ session_id: sessionId }));
  cancelRun.mockImplementation(async (sessionId: string, runId: string) => ({
    session_id: sessionId,
    run_id: runId,
    cancelled: true,
  }));
  // Default: an empty saved-chat list, resolved so boot hydration never
  // hangs a test that doesn't care about it.
  listSavedSessions.mockResolvedValue({ sessions: [] });
});

describe("AppState — B4C boot hydration", () => {
  it("merges a real saved session into the sidebar without auto-opening it", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    renderHarness();

    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    expect(latest.state.chats.s1.backendSessionId).toBe("s1");
    expect(latest.state.chats.s1.title).toBe("B4B Live Persistence Test");
    expect(latest.state.chats.s1.historyHydrationStatus).toBe("unloaded");
    expect(latest.state.activeChatId).toBeNull();
    // No history fetched at boot — summaries only.
    expect(getSessionHistory).not.toHaveBeenCalled();
    // Hydration never creates a backend session.
    expect(createSession).not.toHaveBeenCalled();
  });

  it("boot list failure leaves the app usable with no saved chats, no crash", async () => {
    listSavedSessions.mockRejectedValue(new ApiError("boom", 500));
    renderHarness();

    await act(async () => {
      await Promise.resolve();
    });

    expect(Object.keys(latest.state.chats)).toEqual([]);
    expect(latest.state.activeChatId).toBeNull();
    // The app is still fully usable — sending a message still works.
    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.setDraftText("hello");
    });
    await act(async () => {
      latest.sendMessage();
    });
    expect(createSession).toHaveBeenCalledOnce();
  });
});

describe("AppState — B4C lazy history hydration", () => {
  function seedOneSavedSession() {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
  }

  it("fetches history exactly once when the hydrated chat is selected, not at boot", async () => {
    seedOneSavedSession();
    getSessionHistory.mockResolvedValue({
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
    });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
    expect(getSessionHistory).not.toHaveBeenCalled();

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));

    expect(getSessionHistory).toHaveBeenCalledOnce();
    expect(getSessionHistory).toHaveBeenCalledWith("s1");
    expect(latest.state.chats.s1.messageIds).toEqual(["e-1:user", "e-1:assistant"]);
    expect(latest.state.messages["e-1:user"].text).toBe(
      "B4B live persistence retry. Reply with: B4B smoke confirmed.",
    );
    expect(latest.state.messages["e-1:assistant"].text).toBe("B4B smoke confirmed.");

    // Reselecting the now-loaded chat must not refetch.
    await act(async () => {
      latest.selectChat("s1");
    });
    expect(getSessionHistory).toHaveBeenCalledOnce();
  });

  it("renders a genuinely failed turn as user-only, no fabricated assistant message", async () => {
    seedOneSavedSession();
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
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
    });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));

    expect(latest.state.chats.s1.messageIds).toEqual(["e-2:user"]);
  });

  it("a history load failure remains retryable and does not fabricate 'No messages'", async () => {
    seedOneSavedSession();
    getSessionHistory.mockRejectedValueOnce(new ApiError("This conversation could not be found.", 404));
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("error"));
    expect(latest.state.chats.s1.historyError).toBe("This conversation could not be found.");
    expect(latest.state.chats.s1.messageIds).toEqual([]);

    getSessionHistory.mockResolvedValueOnce({
      session_id: "s1",
      messages: [
        {
          message_id: "e-3:user",
          turn_id: "e-3",
          role: "user",
          text: "retry text",
          created_at: "2026-09-07T21:46:46.130347+00:00",
          attachments: [],
        },
      ],
    });
    await act(async () => {
      latest.retryHistoryLoad("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));
    expect(latest.state.chats.s1.messageIds).toEqual(["e-3:user"]);
  });

  it("a malformed created_at fails the whole history request rather than inventing a timestamp", async () => {
    seedOneSavedSession();
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-4:user",
          turn_id: "e-4",
          role: "user",
          text: "bad timestamp",
          created_at: "not-a-real-date",
          attachments: [],
        },
      ],
    });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("error"));
    expect(latest.state.chats.s1.messageIds).toEqual([]);
  });

  it("switching away from a chat mid-fetch is safe — a late response never changes activeChatId or resurrects a deleted chat", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [
        { session_id: "s1", title: "Chat One", updated_at: "2026-09-07T22:25:09.620902+00:00" },
        { session_id: "s2", title: "Chat Two", updated_at: "2026-09-07T21:00:00.000000+00:00" },
      ],
    });
    let resolveHistory: (value: unknown) => void = () => {};
    getSessionHistory.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHistory = resolve;
        }),
    );
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.selectChat("s1");
    });
    expect(latest.state.chats.s1.historyHydrationStatus).toBe("loading");

    // User navigates away, then deletes chat s1, before the fetch resolves.
    await act(async () => {
      latest.selectChat("s2");
    });
    await act(async () => {
      latest.deleteChat("s1");
    });

    await act(async () => {
      resolveHistory({
        session_id: "s1",
        messages: [
          {
            message_id: "e-5:user",
            turn_id: "e-5",
            role: "user",
            text: "late",
            created_at: "2026-09-07T21:46:46.130347+00:00",
            attachments: [],
          },
        ],
      });
      await Promise.resolve();
    });

    expect(latest.state.activeChatId).toBe("s2");
    expect(latest.state.chats.s1).toBeUndefined();
  });
});

describe("AppState — B4C resume saved chat", () => {
  it("sending a normal message in a hydrated chat uses the existing backendSessionId, never createSession", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
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
    });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));

    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "second reply" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);
    await act(async () => {
      latest.setDraftText("one more message");
    });
    await act(async () => {
      latest.sendMessage();
    });

    expect(createSession).not.toHaveBeenCalled();
    expect(runBackendChat).toHaveBeenCalledWith("s1", "one more message", expect.anything(), expect.anything());
    expect(latest.state.activeChatId).toBe("s1");
    // No duplicate chat was created for the resumed conversation.
    expect(Object.keys(latest.state.chats)).toEqual(["s1"]);
  });
});

describe("AppState — B4C real-chat rename", () => {
  it("a chat with no backendSessionId (a Project-scoped chat — the mock path) renames locally, no PATCH", async () => {
    renderHarness();
    await act(async () => {
      latest.createProject("Test Project");
    });
    await act(async () => {
      latest.setDraftText("hello from a project chat");
    });
    await act(async () => {
      latest.sendMessage();
    });
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].backendSessionId).toBeUndefined();

    await act(async () => {
      latest.renameChat(chatId, "Renamed locally");
    });
    expect(renameSession).not.toHaveBeenCalled();
    expect(latest.state.chats[chatId].title).toBe("Renamed locally");
  });

  it("a hydrated real chat's rename calls PATCH and applies the server-echoed title only on success", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    renameSession.mockResolvedValue({ session_id: "s1", title: "B4C UI Rename Test", updated_at: "2026-09-07T22:25:09.620902+00:00" });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.renameChat("s1", "B4C UI Rename Test");
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(renameSession).toHaveBeenCalledWith("s1", "B4C UI Rename Test");
    expect(latest.state.chats.s1.title).toBe("B4C UI Rename Test");
  });

  it("a failed rename keeps the prior title (no ApiError message printed raw, no permanent unpersisted state)", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    renameSession.mockRejectedValue(new ApiError("This chat could not be renamed right now. Please try again.", 500));
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.renameChat("s1", "Attempted rename");
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(latest.state.chats.s1.title).toBe("B4B Live Persistence Test");
    expect(alertSpy).toHaveBeenCalledWith("This chat could not be renamed right now. Please try again.");
    alertSpy.mockRestore();
  });

  it("a stale rename response cannot overwrite a newer rename for the same chat", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "Original", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    let resolveFirst: (value: unknown) => void = () => {};
    renameSession
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({ session_id: "s1", title: "Second Rename", updated_at: "2026-09-07T22:25:09.620902+00:00" });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.renameChat("s1", "First Rename");
    });
    await act(async () => {
      latest.renameChat("s1", "Second Rename");
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(latest.state.chats.s1.title).toBe("Second Rename");

    // The first (stale) request now resolves — must not clobber the newer title.
    await act(async () => {
      resolveFirst({ session_id: "s1", title: "First Rename", updated_at: "2026-09-07T22:25:09.620902+00:00" });
      await Promise.resolve();
    });
    expect(latest.state.chats.s1.title).toBe("Second Rename");
  });
});

describe("AppState — B4C StrictMode boot idempotency", () => {
  it("never produces a duplicate chat or a duplicate history fetch under React 18 StrictMode double-invocation", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "hi",
          created_at: "2026-09-07T22:25:09.620902+00:00",
          attachments: [],
        },
      ],
    });

    render(
      <StrictMode>
        <AppStateProvider>
          <Harness />
        </AppStateProvider>
      </StrictMode>,
    );

    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
    expect(Object.keys(latest.state.chats)).toEqual(["s1"]);
    expect(latest.state.chatOrder).toEqual(["s1"]);

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));
    expect(getSessionHistory).toHaveBeenCalledOnce();
  });
});

describe("AppState — B4C edit/rewind regression on a hydrated transcript", () => {
  it("editing a hydrated historical user message rewinds using the existing backendSessionId and truncates correctly, without creating a new session", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "Two Turn Chat", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "first message",
          created_at: "2026-09-07T22:00:00.000000+00:00",
          attachments: [],
        },
        {
          message_id: "e-1:assistant",
          turn_id: "e-1",
          role: "assistant",
          text: "first answer",
          created_at: "2026-09-07T22:00:02.000000+00:00",
          attachments: [],
        },
        {
          message_id: "e-2:user",
          turn_id: "e-2",
          role: "user",
          text: "second message",
          created_at: "2026-09-07T22:05:00.000000+00:00",
          attachments: [],
        },
        {
          message_id: "e-2:assistant",
          turn_id: "e-2",
          role: "assistant",
          text: "second answer",
          created_at: "2026-09-07T22:05:02.000000+00:00",
          attachments: [],
        },
      ],
    });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));

    playEvents([
      { type: "run.started", data: {} },
      { type: "message.completed", data: { content: "edited answer" } },
      { type: "run.completed", data: { outcome: "ok" } },
    ]);

    await act(async () => {
      latest.editMessage("e-1:user", "first message, edited");
      // Let the rewindSession() await resolve and the follow-up dispatch run.
      await Promise.resolve();
      await Promise.resolve();
    });

    // beforeUserTurnIndex must count exactly the ONE user turn preceding
    // "e-1:user" — i.e. zero, since it's the chat's first message.
    expect(rewindSession).toHaveBeenCalledWith("s1", 0);
    expect(createSession).not.toHaveBeenCalled();
    // The edited turn and everything after it (the original second turn)
    // is truncated from the visible transcript.
    expect(latest.state.chats.s1.messageIds).not.toContain("e-2:user");
    expect(latest.state.chats.s1.messageIds).not.toContain("e-2:assistant");
    expect(latest.state.messages["e-1:user"].text).toBe("first message, edited");
    expect(runBackendChat).toHaveBeenCalledWith("s1", "first message, edited", expect.anything(), expect.anything());
  });
});

describe("AppState — B4C correction pass: global savedChatsHydrationStatus", () => {
  it("starts as 'loading' the instant the provider mounts, before the GET resolves", async () => {
    let resolveList: (value: unknown) => void = () => {};
    listSavedSessions.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveList = resolve;
        }),
    );
    renderHarness();
    expect(latest.state.savedChatsHydrationStatus).toBe("loading");
    // Cleanup: resolve so this test doesn't leave a dangling promise.
    await act(async () => {
      resolveList({ sessions: [] });
      await Promise.resolve();
    });
  });

  it("a successful GET with real sessions settles to 'loaded'", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    renderHarness();
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("loaded"));
    expect(latest.state.chats.s1).toBeDefined();
  });

  it("a successful GET with an empty sessions array settles to 'loaded' with a genuine empty list, never 'error'", async () => {
    listSavedSessions.mockResolvedValue({ sessions: [] });
    renderHarness();
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("loaded"));
    expect(Object.keys(latest.state.chats)).toEqual([]);
  });

  it("a boot failure settles to 'error' with a safe message and does not delete any existing local chat", async () => {
    listSavedSessions.mockRejectedValue(new ApiError("Couldn't load saved chats.", 500));
    renderHarness();
    await act(async () => {
      latest.createProject("Local Project");
    });
    await act(async () => {
      latest.setDraftText("a local project message");
    });
    await act(async () => {
      latest.sendMessage();
    });
    const localChatId = latest.state.activeChatId!;

    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("error"));
    expect(latest.state.savedChatsHydrationError).toBe("Couldn't load saved chats.");
    expect(latest.state.chats[localChatId]).toBeDefined();
  });

  it("retrySavedChats calls GET /api/sessions again and can recover from error to loaded", async () => {
    listSavedSessions.mockRejectedValueOnce(new ApiError("Couldn't load saved chats.", 500));
    renderHarness();
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("error"));
    expect(listSavedSessions).toHaveBeenCalledOnce();

    listSavedSessions.mockResolvedValueOnce({
      sessions: [{ session_id: "s1", title: "Recovered Chat", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    await act(async () => {
      latest.retrySavedChats();
    });
    expect(listSavedSessions).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("loaded"));
    expect(latest.state.chats.s1).toBeDefined();
    expect(latest.state.chats.s1.title).toBe("Recovered Chat");
  });

  it("a failed retry remains in the error state with the new failure's message", async () => {
    listSavedSessions.mockRejectedValueOnce(new ApiError("First failure.", 500));
    renderHarness();
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("error"));

    listSavedSessions.mockRejectedValueOnce(new ApiError("Second failure.", 500));
    await act(async () => {
      latest.retrySavedChats();
    });
    await waitFor(() => expect(latest.state.savedChatsHydrationError).toBe("Second failure."));
    expect(latest.state.savedChatsHydrationStatus).toBe("error");
  });

  it("repeated retry/success cycles never duplicate a backendSessionId's chat", async () => {
    const sessions = [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }];
    listSavedSessions.mockResolvedValue({ sessions });
    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    await act(async () => {
      latest.retrySavedChats();
      await Promise.resolve();
    });
    await act(async () => {
      latest.retrySavedChats();
      await Promise.resolve();
    });

    expect(Object.keys(latest.state.chats)).toEqual(["s1"]);
    expect(latest.state.chatOrder).toEqual(["s1"]);
  });

  it("StrictMode double-invocation at boot still settles to exactly one GET and the correct final status", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    render(
      <StrictMode>
        <AppStateProvider>
          <Harness />
        </AppStateProvider>
      </StrictMode>,
    );
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("loaded"));
    expect(listSavedSessions).toHaveBeenCalledOnce();
    expect(Object.keys(latest.state.chats)).toEqual(["s1"]);

    // An explicit retry after StrictMode's boot settles must still work —
    // the boot single-flight ref must never permanently block it.
    await act(async () => {
      latest.retrySavedChats();
    });
    expect(listSavedSessions).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(latest.state.savedChatsHydrationStatus).toBe("loaded"));
  });
});
