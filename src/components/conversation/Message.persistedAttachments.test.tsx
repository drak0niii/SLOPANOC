import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mirrors AppState.savedChats.integration.test.tsx's own mock shape for
// everything AppState.tsx itself imports at module scope — this file
// renders a REAL AppStateProvider (not a mocked useAppState) so it can
// exercise the real boot -> select -> lazy history -> persisted-image
// pipeline end to end, the same way a real saved-chat session would.
const createSession = vi.fn();
const rewindSession = vi.fn();
const cancelRun = vi.fn();
const listSavedSessions = vi.fn();
const getSessionHistory = vi.fn();
const renameSession = vi.fn();
vi.mock("../../api/sessions", () => ({
  createSession: (...args: unknown[]) => createSession(...args),
  rewindSession: (...args: unknown[]) => rewindSession(...args),
  cancelRun: (...args: unknown[]) => cancelRun(...args),
  listSavedSessions: (...args: unknown[]) => listSavedSessions(...args),
  getSessionHistory: (...args: unknown[]) => getSessionHistory(...args),
  renameSession: (...args: unknown[]) => renameSession(...args),
}));

const runBackendChat = vi.fn();
vi.mock("../../api/runBackendChat", () => ({ runBackendChat: (...args: unknown[]) => runBackendChat(...args) }));

const chooseSelection = vi.fn();
const skipSelection = vi.fn();
vi.mock("../../api/selections", () => ({
  chooseSelection: (...args: unknown[]) => chooseSelection(...args),
  skipSelection: (...args: unknown[]) => skipSelection(...args),
}));

const uploadAttachment = vi.fn();
const getAttachmentContent = vi.fn();
vi.mock("../../api/attachments", () => ({
  uploadAttachment: (...args: unknown[]) => uploadAttachment(...args),
  getAttachmentContent: (...args: unknown[]) => getAttachmentContent(...args),
}));

import { AppStateProvider, useAppState } from "../../state/AppState";
import { TooltipProvider } from "../ui/Tooltip";
import { Message } from "./Message";

if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}

let latest: ReturnType<typeof useAppState>;
function Harness() {
  latest = useAppState();
  return null;
}

/** Minimal stand-in for MessageList — renders exactly the active chat's
 * messages via the real Message component, without pulling in
 * MessageList's own scroll-following ResizeObserver machinery (untested
 * elsewhere and irrelevant to this file's persisted-attachment concern). */
function ActiveMessages() {
  const { activeChat, state } = useAppState();
  if (!activeChat) return null;
  return (
    <>
      {activeChat.messageIds.map((id) => {
        const message = state.messages[id];
        return message ? <Message key={id} message={message} /> : null;
      })}
    </>
  );
}

function renderHarness() {
  render(
    <AppStateProvider>
      <TooltipProvider>
        <Harness />
        <ActiveMessages />
      </TooltipProvider>
    </AppStateProvider>,
  );
}

function pngBlob(): Blob {
  return new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
}

beforeEach(() => {
  createSession.mockReset();
  rewindSession.mockReset();
  cancelRun.mockReset();
  listSavedSessions.mockReset();
  getSessionHistory.mockReset();
  renameSession.mockReset();
  runBackendChat.mockReset();
  chooseSelection.mockReset();
  skipSelection.mockReset();
  uploadAttachment.mockReset();
  getAttachmentContent.mockReset();
  listSavedSessions.mockResolvedValue({ sessions: [] });
});

describe("Message + PersistedImageAttachment — real AppState integration", () => {
  it("history JSON success + one image content failure: historyHydrationStatus remains 'loaded', text transcript unaffected", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "Fixture Chat", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "here's a screenshot",
          created_at: "2026-09-07T22:25:09.620902+00:00",
          attachments: [
            { attachment_id: "att-1", filename: "screenshot.png", mime_type: "image/png", size_bytes: 12345 },
          ],
        },
        {
          message_id: "e-1:assistant",
          turn_id: "e-1",
          role: "assistant",
          text: "Got it.",
          created_at: "2026-09-07T22:25:12.000000+00:00",
          attachments: [],
        },
      ],
    });
    getAttachmentContent.mockRejectedValue(new Error("content fetch failed"));

    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());

    // Lazy network boundary: no content GET merely from boot/summary hydration.
    expect(getAttachmentContent).not.toHaveBeenCalled();
    expect(getSessionHistory).not.toHaveBeenCalled();

    await act(async () => {
      latest.selectChat("s1");
    });
    await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));
    // History text is visible.
    expect(screen.getByText("here's a screenshot")).toBeInTheDocument();
    expect(screen.getByText("Got it.")).toBeInTheDocument();

    // The image content fetch happens only now, because Message actually rendered it.
    await waitFor(() => expect(getAttachmentContent).toHaveBeenCalledTimes(1));
    expect(getAttachmentContent).toHaveBeenCalledWith("att-1", expect.anything());

    await waitFor(() => expect(screen.getByText("Image unavailable")).toBeInTheDocument());

    // The image failure must NEVER flip history back to "error" — separate boundaries.
    expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded");
    // Text transcript remains intact despite the image failure.
    expect(screen.getByText("here's a screenshot")).toBeInTheDocument();
    expect(screen.getByText("Got it.")).toBeInTheDocument();
  });

  it("a text-only saved chat (no attachments) never issues a single content GET", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "Text Only", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "hello",
          created_at: "2026-09-07T22:25:09.620902+00:00",
          attachments: [],
        },
        {
          message_id: "e-1:assistant",
          turn_id: "e-1",
          role: "assistant",
          text: "hi",
          created_at: "2026-09-07T22:25:12.000000+00:00",
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

    expect(getAttachmentContent).not.toHaveBeenCalled();
  });

  it("a successful persisted image renders inside the owning user message", async () => {
    listSavedSessions.mockResolvedValue({
      sessions: [{ session_id: "s1", title: "Fixture Chat", updated_at: "2026-09-07T22:25:09.620902+00:00" }],
    });
    getSessionHistory.mockResolvedValue({
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "here's a screenshot",
          created_at: "2026-09-07T22:25:09.620902+00:00",
          attachments: [
            { attachment_id: "att-1", filename: "screenshot.png", mime_type: "image/png", size_bytes: 12345 },
          ],
        },
      ],
    });
    getAttachmentContent.mockResolvedValue(pngBlob());

    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
    await act(async () => {
      latest.selectChat("s1");
    });

    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("alt", "Attached image: screenshot.png");
  });
});

describe("Message + AppState — B4D edit/rewind ghost-attachment regression", () => {
  it("discarding a later turn via edit removes its persisted attachment entirely — no ghost under another message", async () => {
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
          text: "second message with an image",
          created_at: "2026-09-07T22:05:00.000000+00:00",
          attachments: [
            { attachment_id: "att-1", filename: "second.png", mime_type: "image/png", size_bytes: 999 },
          ],
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
    getAttachmentContent.mockResolvedValue(pngBlob());
    rewindSession.mockResolvedValue({ session_id: "s1" });
    runBackendChat.mockImplementation(async (_sessionId, _message, handlers) => {
      handlers.onRunStarted("server-run-1");
      handlers.onCompleted("edited answer");
      handlers.onRunCompleted("ok");
    });

    renderHarness();
    await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
    await act(async () => {
      latest.selectChat("s1");
    });
    // The second turn's image is rendered before the edit.
    await screen.findByRole("img");
    expect(latest.state.messages["e-2:user"].persistedAttachments).toHaveLength(1);

    await act(async () => {
      latest.editMessage("e-1:user", "first message, edited");
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(rewindSession).toHaveBeenCalledWith("s1", 0);
    expect(createSession).not.toHaveBeenCalled();
    // The discarded second turn (and its persisted attachment) is gone entirely.
    expect(latest.state.messages["e-2:user"]).toBeUndefined();
    expect(latest.state.chats.s1.messageIds).not.toContain("e-2:user");
    // Its image renderer unmounted — no ghost <img> for the removed attachment.
    expect(screen.queryByAltText("Attached image: second.png")).not.toBeInTheDocument();
  });
});
