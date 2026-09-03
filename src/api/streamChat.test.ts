import { afterEach, describe, expect, it, vi } from "vitest";
import { isAbortError, streamChatMessage } from "./streamChat";
import { ApiError } from "./client";
import type { SSEEvent } from "./types";

function sseFrame(type: string, data: unknown, sequence: number): string {
  return `event: ${type}\ndata: ${JSON.stringify({
    type,
    session_id: "s1",
    run_id: "r1",
    sequence,
    timestamp: "2026-01-01T00:00:00Z",
    data,
  })}\n\n`;
}

function bodyStreamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index]));
      index += 1;
    },
  });
}

function mockFetchOnce(response: { ok?: boolean; status?: number; body?: ReadableStream<Uint8Array> | null }) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: null,
      ...response,
    } as unknown as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChatMessage", () => {
  it("delivers events in order via onEvent", async () => {
    const chunks = [
      sseFrame("run.started", {}, 1),
      sseFrame("status", { stage: "processing", label: "Processing your request", presentation: "replace" }, 2),
      sseFrame("message.completed", { content: "hi" }, 3),
      sseFrame("run.completed", { outcome: "ok" }, 4),
    ];
    mockFetchOnce({ body: bodyStreamFromChunks(chunks) });

    const events: SSEEvent[] = [];
    await streamChatMessage({
      sessionId: "s1",
      message: "hello",
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(events.map((e) => e.type)).toEqual(["run.started", "status", "message.completed", "run.completed"]);
  });

  it("handles multiple frames delivered within a single chunk", async () => {
    const combined = sseFrame("run.started", {}, 1) + sseFrame("run.completed", { outcome: "ok" }, 2);
    mockFetchOnce({ body: bodyStreamFromChunks([combined]) });

    const events: SSEEvent[] = [];
    await streamChatMessage({
      sessionId: "s1",
      message: "hi",
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    });

    expect(events.map((e) => e.type)).toEqual(["run.started", "run.completed"]);
  });

  it("throws a normalized ApiError for a non-2xx response, never leaking raw body text", async () => {
    mockFetchOnce({ ok: false, status: 404, body: null });

    await expect(
      streamChatMessage({
        sessionId: "does-not-exist",
        message: "hi",
        signal: new AbortController().signal,
        onEvent: () => {},
      }),
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("resolves cleanly (no throw) when the stream ends mid-frame", async () => {
    const chunks = [sseFrame("run.started", {}, 1), 'event: status\ndata: {"stage":"processing"']; // truncated, no closing blank line
    mockFetchOnce({ body: bodyStreamFromChunks(chunks) });

    const events: SSEEvent[] = [];
    await expect(
      streamChatMessage({
        sessionId: "s1",
        message: "hi",
        signal: new AbortController().signal,
        onEvent: (event) => events.push(event),
      }),
    ).resolves.toBeUndefined();
    expect(events.map((e) => e.type)).toEqual(["run.started"]);
  });

  it("propagates an AbortError distinguishably when the request is aborted", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      }),
    );

    const promise = streamChatMessage({
      sessionId: "s1",
      message: "hi",
      signal: controller.signal,
      onEvent: () => {},
    });
    controller.abort();

    await expect(promise).rejects.toSatisfy((error: unknown) => isAbortError(error));
  });
});
