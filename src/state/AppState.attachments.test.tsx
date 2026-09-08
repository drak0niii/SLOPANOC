import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// POST-5.1 B3 — this file exercises the real attachment lifecycle end to
// end (queueImageFiles -> ensureBackendSession -> uploadAttachment ->
// reducer state), mocking only the two network boundaries it actually
// crosses. Mirrors AppState.integration.test.tsx's own harness pattern
// exactly, so this file's `createSession`/`uploadAttachment` mocking is
// the ONLY thing that differs from that file's established convention.
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

const uploadAttachment = vi.fn();
vi.mock("../api/attachments", () => ({
  uploadAttachment: (...args: unknown[]) => uploadAttachment(...args),
}));

import { ApiError } from "../api/client";
import { AppStateProvider, useAppState } from "./AppState";
import type { DraftImageAttachment } from "../types";

// jsdom does not implement these — every test in this file goes through
// `queueImageFiles`, which calls `URL.createObjectURL` unconditionally, so
// a minimal polyfill is required just to exercise the code at all. Real
// browser behavior is separately verified in the B3 manual validation
// pass (see the final report), not by this polyfill's own fidelity.
let objectUrlCounter = 0;
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => `blob:mock-${objectUrlCounter++}`;
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
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

function pngFile(name = "photo.png", bytes = 100): File {
  return new File([new Uint8Array(bytes)], name, { type: "image/png" });
}

function imageDrafts(): DraftImageAttachment[] {
  return latest.state.draft.attachments.filter((a): a is DraftImageAttachment => a.kind === "image");
}

/** Resolves whenever a same-tick microtask queue needs draining, without
 * assuming how many hops a given promise chain takes — mirrors the
 * existing integration test file's own flush idiom. */
async function flush(times = 4) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  createSession.mockReset();
  runBackendChat.mockReset();
  rewindSession.mockReset();
  chooseSelection.mockReset();
  skipSelection.mockReset();
  cancelRun.mockReset();
  uploadAttachment.mockReset();
  createSession.mockImplementation(async () => ({ session_id: `session-${createSession.mock.calls.length}` }));
});

describe("AppState — queueImageFiles eligibility and preflight", () => {
  it("does nothing when called with an empty file list", () => {
    renderHarness();
    act(() => {
      latest.queueImageFiles([]);
    });
    expect(latest.state.activeChatId).toBeNull();
  });

  it("creates a brand-new draft chat for the first image attached with no active chat, mirroring 'first prompt creates the conversation'", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(() => new Promise(() => {}));
    expect(latest.state.activeChatId).toBeNull();

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();

    expect(latest.state.activeChatId).not.toBeNull();
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId]).toBeDefined();
    expect(latest.state.chats[chatId].messageIds).toEqual([]);
  });

  it("marks an unsupported file type as failed locally, without ever calling uploadAttachment", async () => {
    renderHarness();
    const gif = new File([new Uint8Array(10)], "clip.gif", { type: "image/gif" });

    act(() => {
      latest.queueImageFiles([gif]);
    });
    await flush();

    const drafts = imageDrafts();
    expect(drafts).toHaveLength(1);
    expect(drafts[0].uploadState).toBe("failed");
    expect(drafts[0].error).toBe("This image format isn't supported.");
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it("marks an oversized file as failed locally, without ever calling uploadAttachment", async () => {
    renderHarness();
    const big = pngFile("huge.png", 9 * 1024 * 1024);

    act(() => {
      latest.queueImageFiles([big]);
    });
    await flush();

    const drafts = imageDrafts();
    expect(drafts[0].uploadState).toBe("failed");
    expect(drafts[0].error).toBe("Image is too large.");
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it("0 existing images + selecting 5: accepts only up to MAX_DRAFT_IMAGES (4), uploads exactly 4, and shows a visible limit notice — never silent", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png"), pngFile("e.png")]);
    });
    await flush();

    expect(imageDrafts()).toHaveLength(4);
    expect(uploadAttachment).toHaveBeenCalledTimes(4);
    expect(latest.state.draft.attachmentLimitNotice?.message).toBe("Up to 4 images can be attached.");
  });

  it("3 existing images + selecting 3 more: accepts exactly 1, uploads exactly 1 (never the 2 rejected), and shows the notice", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png")]);
    });
    await flush();
    expect(imageDrafts()).toHaveLength(3);
    expect(latest.state.draft.attachmentLimitNotice).toBeNull();

    uploadAttachment.mockClear();
    act(() => {
      latest.queueImageFiles([pngFile("d.png"), pngFile("e.png"), pngFile("f.png")]);
    });
    await flush();

    expect(imageDrafts()).toHaveLength(4);
    expect(uploadAttachment).toHaveBeenCalledTimes(1);
    expect(latest.state.draft.attachmentLimitNotice?.message).toBe("Up to 4 images can be attached.");
  });

  it("4 existing images + pasting one more: nothing is queued, uploadAttachment is never called, and the notice appears", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png")]);
    });
    await flush();
    expect(imageDrafts()).toHaveLength(4);

    uploadAttachment.mockClear();
    act(() => {
      // Simulates a paste attempt (a single file, the shape PromptComposer's
      // paste handler passes) while already at capacity.
      latest.queueImageFiles([pngFile("e.png")]);
    });
    await flush();

    expect(imageDrafts()).toHaveLength(4);
    expect(uploadAttachment).not.toHaveBeenCalled();
    expect(latest.state.draft.attachmentLimitNotice?.message).toBe("Up to 4 images can be attached.");
  });

  it("removing one image after the limit notice restores normal queueing (a 5th image can now be added)", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png")]);
    });
    await flush();
    act(() => {
      latest.queueImageFiles([pngFile("e.png")]);
    });
    await flush();
    expect(latest.state.draft.attachmentLimitNotice).not.toBeNull();

    act(() => {
      latest.removeAttachment(imageDrafts()[0].id);
    });
    await flush();

    uploadAttachment.mockClear();
    act(() => {
      latest.queueImageFiles([pngFile("f.png")]);
    });
    await flush();

    expect(imageDrafts()).toHaveLength(4);
    expect(uploadAttachment).toHaveBeenCalledTimes(1);
  });

  it("a long-paste text attachment never counts toward the 4-image capacity", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.addAttachments([
        {
          id: "att-1",
          kind: "file",
          name: "Pasted text.txt",
          meta: "500 characters",
          isPastedText: true,
          content: "x",
        },
      ]);
    });
    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png")]);
    });
    await flush();

    expect(imageDrafts()).toHaveLength(4);
    expect(latest.state.draft.attachmentLimitNotice).toBeNull();
  });

  it("is a no-op when a chat-room source is already drafted (backend branch ineligible)", () => {
    renderHarness();
    act(() => {
      latest.addDraftSource({ id: "src-1", kind: "teams_channel", scope: "General" });
    });
    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    expect(imageDrafts()).toHaveLength(0);
  });
});

describe("AppState — real image upload lifecycle", () => {
  it("transitions pending -> uploading -> ready and stores the backend attachment_id", async () => {
    renderHarness();
    let resolveUpload!: (v: unknown) => void;
    uploadAttachment.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    expect(imageDrafts()[0].uploadState).toBe("uploading");

    await act(async () => {
      resolveUpload({
        attachment_id: "att-123",
        filename: "photo.png",
        mime_type: "image/png",
        size_bytes: 100,
        status: "ready",
      });
      await Promise.resolve();
    });

    expect(imageDrafts()[0].uploadState).toBe("ready");
    expect(imageDrafts()[0].attachmentId).toBe("att-123");
  });

  it("ensures a real backend session exists before the first upload starts", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();

    expect(createSession).toHaveBeenCalledOnce();
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].backendSessionId).toBe("session-1");
    expect(uploadAttachment).toHaveBeenCalledWith("session-1", expect.any(File), expect.anything());
  });

  it("session creation is single-flight: two images queued before the session resolves cause exactly one POST /api/sessions", async () => {
    renderHarness();
    let resolveSession!: (v: { session_id: string }) => void;
    createSession.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSession = resolve;
        }),
    );
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    // Both images are queued together, synchronously, before the (still
    // in-flight) session promise resolves — the exact race the single-
    // flight cache exists to prevent.
    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png")]);
    });
    await flush();
    expect(createSession).toHaveBeenCalledOnce();

    await act(async () => {
      resolveSession({ session_id: "session-1" });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createSession).toHaveBeenCalledOnce();
    expect(uploadAttachment).toHaveBeenCalledTimes(2);
    expect(uploadAttachment).toHaveBeenCalledWith("session-1", expect.any(File), expect.anything());
  });

  it("session creation is single-flight for a THREE-image selection: exactly one POST /api/sessions, all three uploads use the same session", async () => {
    renderHarness();
    let resolveSession!: (v: { session_id: string }) => void;
    createSession.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSession = resolve;
        }),
    );
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-x",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png")]);
    });
    await flush();
    expect(createSession).toHaveBeenCalledOnce();

    await act(async () => {
      resolveSession({ session_id: "session-1" });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createSession).toHaveBeenCalledOnce();
    expect(uploadAttachment).toHaveBeenCalledTimes(3);
    for (const call of uploadAttachment.mock.calls) {
      expect(call[0]).toBe("session-1");
    }
    expect(imageDrafts().every((d) => d.uploadState === "ready")).toBe(true);
  });

  it("two independent queueImageFiles calls (e.g. two rapid paste events) before the session resolves still cause exactly one POST /api/sessions", async () => {
    renderHarness();
    let resolveSession!: (v: { session_id: string }) => void;
    createSession.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSession = resolve;
        }),
    );
    uploadAttachment.mockImplementation(async () => ({
      attachment_id: "att-y",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    }));

    // Two SEPARATE calls into the central ingestion path — mirrors two
    // distinct paste events fired back-to-back — rather than one call with
    // multiple files, since the single-flight cache must dedupe across
    // calls, not just within one.
    act(() => {
      latest.queueImageFiles([pngFile("a.png")]);
    });
    act(() => {
      latest.queueImageFiles([pngFile("b.png")]);
    });
    await flush();
    expect(createSession).toHaveBeenCalledOnce();

    await act(async () => {
      resolveSession({ session_id: "session-1" });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createSession).toHaveBeenCalledOnce();
    expect(uploadAttachment).toHaveBeenCalledTimes(2);
    for (const call of uploadAttachment.mock.calls) {
      expect(call[0]).toBe("session-1");
    }
  });

  it("maps a 413 upload failure to the safe 'Image is too large.' message", async () => {
    renderHarness();
    uploadAttachment.mockRejectedValue(
      new ApiError("ignored", 413, { errorCode: "payload_too_large", retryable: false, correlationId: "c1" }),
    );

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();

    expect(imageDrafts()[0].uploadState).toBe("failed");
    expect(imageDrafts()[0].error).toBe("Image is too large.");
  });

  it("retryImageAttachment reuses the exact same File and objectUrl — never re-reads or re-previews", async () => {
    renderHarness();
    uploadAttachment.mockRejectedValueOnce(new Error("network down"));

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    const draftId = imageDrafts()[0].id;
    const originalFile = imageDrafts()[0].file;
    const originalObjectUrl = imageDrafts()[0].objectUrl;
    expect(imageDrafts()[0].uploadState).toBe("failed");

    uploadAttachment.mockResolvedValueOnce({
      attachment_id: "att-retry",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    });
    act(() => {
      latest.retryImageAttachment(draftId);
    });
    await flush();

    expect(imageDrafts()[0].uploadState).toBe("ready");
    expect(imageDrafts()[0].file).toBe(originalFile);
    expect(imageDrafts()[0].objectUrl).toBe(originalObjectUrl);
    expect(uploadAttachment.mock.calls[1][1]).toBe(originalFile);
  });

  it("removing an attachment mid-upload aborts the in-flight request", async () => {
    renderHarness();
    let sawAbort = false;
    uploadAttachment.mockImplementation(
      (_session: string, _file: File, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            sawAbort = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    const draftId = imageDrafts()[0].id;

    act(() => {
      latest.removeAttachment(draftId);
    });
    await flush();

    expect(sawAbort).toBe(true);
    expect(imageDrafts()).toHaveLength(0);
  });

  it("a stale upload completion after removal never resurrects the removed attachment", async () => {
    renderHarness();
    let resolveUpload!: (v: unknown) => void;
    uploadAttachment.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    const draftId = imageDrafts()[0].id;
    expect(imageDrafts()).toHaveLength(1);

    act(() => {
      latest.removeAttachment(draftId);
    });
    expect(imageDrafts()).toHaveLength(0);

    // The upload's promise resolves AFTER removal — a real race, since
    // removeAttachment's abort() doesn't guarantee the in-flight fetch's
    // executor has already checked the signal by the time this fires.
    await act(async () => {
      resolveUpload({
        attachment_id: "att-late",
        filename: "photo.png",
        mime_type: "image/png",
        size_bytes: 100,
        status: "ready",
      });
      await Promise.resolve();
    });

    expect(imageDrafts()).toHaveLength(0);
  });
});

describe("AppState — object URL lifecycle", () => {
  it("creates exactly one object URL per queued image", async () => {
    renderHarness();
    const createSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock");
    uploadAttachment.mockImplementation(() => new Promise(() => {}));

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png")]);
    });
    await flush();

    expect(createSpy).toHaveBeenCalledTimes(2);
    createSpy.mockRestore();
  });

  it("revokes the object URL when its attachment is removed from the draft", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(() => new Promise(() => {}));
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    const objectUrl = imageDrafts()[0].objectUrl;
    const draftId = imageDrafts()[0].id;

    act(() => {
      latest.removeAttachment(draftId);
    });
    await flush();

    expect(revokeSpy).toHaveBeenCalledWith(objectUrl);
    revokeSpy.mockRestore();
  });

  it("does not revoke the object URL of an attachment that is still present", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(() => new Promise(() => {}));
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    act(() => {
      latest.queueImageFiles([pngFile("a.png"), pngFile("b.png")]);
    });
    await flush();
    const [first, second] = imageDrafts();

    act(() => {
      latest.removeAttachment(first.id);
    });
    await flush();

    expect(revokeSpy).toHaveBeenCalledWith(first.objectUrl);
    expect(revokeSpy).not.toHaveBeenCalledWith(second.objectUrl);
    revokeSpy.mockRestore();
  });
});

describe("AppState — POST-5.1 B5 sendMessage eligibility for real image attachments", () => {
  it("sendMessage is a no-op while an image attachment is still uploading", async () => {
    renderHarness();
    uploadAttachment.mockImplementation(() => new Promise(() => {}));

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    act(() => {
      latest.setDraftText("please look at this");
    });

    act(() => {
      latest.sendMessage();
    });
    await flush();

    expect(runBackendChat).not.toHaveBeenCalled();
    expect(createSession).toHaveBeenCalledOnce(); // only from the upload's own ensureBackendSession, not a second send-triggered one
    // No assistant message was fabricated for a send that never happened.
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].messageIds).toEqual([]);
  });

  it("sendMessage is a no-op while an image attachment upload has failed", async () => {
    renderHarness();
    uploadAttachment.mockRejectedValue(new Error("boom"));

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    expect(imageDrafts()[0].uploadState).toBe("failed");

    act(() => {
      latest.setDraftText("please look at this");
    });
    act(() => {
      latest.sendMessage();
    });
    await flush();

    expect(runBackendChat).not.toHaveBeenCalled();
    const chatId = latest.state.activeChatId!;
    expect(latest.state.chats[chatId].messageIds).toEqual([]);
  });
});

describe("AppState — POST-5.1 B5 real image send: immediate persistedAttachments", () => {
  it("sends a ready image with text: Message.persistedAttachments set immediately, attachmentIds forwarded, no File/Blob/objectUrl in the sent message", async () => {
    renderHarness();
    uploadAttachment.mockResolvedValue({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    });

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    expect(imageDrafts()[0].uploadState).toBe("ready");

    act(() => {
      latest.setDraftText("here's a screenshot");
    });
    act(() => {
      latest.sendMessage();
    });
    await flush();

    expect(runBackendChat).toHaveBeenCalledOnce();
    const call = (runBackendChat as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("session-1");
    expect(call[1]).toBe("here's a screenshot");
    expect(call[4]).toEqual(["att-1"]); // attachmentIds, draft order

    const chatId = latest.state.activeChatId!;
    const userMessageId = latest.state.chats[chatId].messageIds[0];
    const userMessage = latest.state.messages[userMessageId];
    expect(userMessage.persistedAttachments).toEqual([
      { attachmentId: "att-1", filename: "photo.png", mimeType: "image/png", sizeBytes: 100 },
    ]);
    // The image went into persistedAttachments, never the legacy mock
    // attachments field (which is empty here since there were no
    // non-image attachments in this draft).
    expect(userMessage.attachments).toEqual([]);
    // No File/Blob/draft objectUrl ever entered the sent message.
    expect(JSON.stringify(userMessage)).not.toMatch(/objectUrl|blob:/);
  });

  it("sends a ready image-only draft (no typed text) — text is empty, image still attached", async () => {
    renderHarness();
    uploadAttachment.mockResolvedValue({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    });

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();

    act(() => {
      latest.sendMessage();
    });
    await flush();

    expect(runBackendChat).toHaveBeenCalledOnce();
    const chatId = latest.state.activeChatId!;
    const userMessageId = latest.state.chats[chatId].messageIds[0];
    const userMessage = latest.state.messages[userMessageId];
    expect(userMessage.text).toBe("");
    expect(userMessage.persistedAttachments).toEqual([
      { attachmentId: "att-1", filename: "photo.png", mimeType: "image/png", sizeBytes: 100 },
    ]);
  });

  it("preserves draft order across multiple images, end to end into persistedAttachments and attachmentIds", async () => {
    renderHarness();
    uploadAttachment
      .mockResolvedValueOnce({ attachment_id: "att-1", filename: "one.png", mime_type: "image/png", size_bytes: 10, status: "ready" })
      .mockResolvedValueOnce({ attachment_id: "att-2", filename: "two.png", mime_type: "image/png", size_bytes: 20, status: "ready" });

    act(() => {
      latest.queueImageFiles([pngFile("one.png"), pngFile("two.png")]);
    });
    await flush();
    expect(imageDrafts()).toHaveLength(2);
    expect(imageDrafts().every((d) => d.uploadState === "ready")).toBe(true);

    act(() => {
      latest.sendMessage();
    });
    await flush();

    const call = (runBackendChat as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[4]).toEqual(["att-1", "att-2"]);

    const chatId = latest.state.activeChatId!;
    const userMessageId = latest.state.chats[chatId].messageIds[0];
    const userMessage = latest.state.messages[userMessageId];
    expect(userMessage.persistedAttachments!.map((a) => a.attachmentId)).toEqual(["att-1", "att-2"]);
  });
});

describe("AppState — POST-5.1 B3 overflow-UX auto-dismiss", () => {
  it("clears the limit notice automatically after ATTACHMENT_LIMIT_NOTICE_DURATION_MS", async () => {
    vi.useFakeTimers();
    try {
      renderHarness();
      uploadAttachment.mockImplementation(() => new Promise(() => {}));

      act(() => {
        latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png"), pngFile("e.png")]);
      });
      await flush();
      expect(latest.state.draft.attachmentLimitNotice).not.toBeNull();

      await act(async () => {
        vi.advanceTimersByTime(4000);
      });

      expect(latest.state.draft.attachmentLimitNotice).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a fresh notice restarts the timer even with identical text (does not inherit the prior notice's remaining time)", async () => {
    vi.useFakeTimers();
    try {
      renderHarness();
      uploadAttachment.mockImplementation(() => new Promise(() => {}));

      act(() => {
        latest.queueImageFiles([pngFile("a.png"), pngFile("b.png"), pngFile("c.png"), pngFile("d.png"), pngFile("e.png")]);
      });
      await flush();
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      // A second overflow with the SAME message text, just before the first
      // timer would have fired.
      act(() => {
        latest.queueImageFiles([pngFile("f.png")]);
      });
      await flush();
      await act(async () => {
        vi.advanceTimersByTime(3000); // 6000ms total, but only 3000ms since the second notice
      });
      expect(latest.state.draft.attachmentLimitNotice).not.toBeNull();

      await act(async () => {
        vi.advanceTimersByTime(1000); // now 4000ms since the second notice
      });
      expect(latest.state.draft.attachmentLimitNotice).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("AppState — POST-5.1 B5/B4D: edit prohibition holds immediately after a real image send", () => {
  it("editMessage on a just-sent image-bearing message is a structural no-op, before any refresh", async () => {
    renderHarness();
    uploadAttachment.mockResolvedValue({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    });

    act(() => {
      latest.queueImageFiles([pngFile()]);
    });
    await flush();
    act(() => {
      latest.setDraftText("here's a screenshot");
    });
    act(() => {
      latest.sendMessage();
    });
    await flush();

    const chatId = latest.state.activeChatId!;
    const userMessageId = latest.state.chats[chatId].messageIds[0];
    expect(latest.state.messages[userMessageId].persistedAttachments).toHaveLength(1);

    const stateBefore = latest.state;
    act(() => {
      latest.editMessage(userMessageId, "trying to edit an image message");
    });
    await flush();

    // No rewind, no new session, nothing dispatched at all.
    expect(rewindSession).not.toHaveBeenCalled();
    expect(createSession).toHaveBeenCalledOnce(); // only the original upload/send session creation
    expect(latest.state.messages[userMessageId].text).toBe("here's a screenshot");
    expect(latest.state).toBe(stateBefore);
  });
});
