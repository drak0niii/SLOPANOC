import { getApiBaseUrl } from "./config";
import { ApiError } from "./client";
import { appendAndSplitFrames, extractDataPayload, parseSSEEvent } from "./sseParser";
import type { SSEEvent } from "./types";

/**
 * Streams one chat turn via `POST /api/sessions/{id}/messages/stream`.
 *
 * Deliberately NOT using the browser `EventSource` API — it only supports
 * GET requests, and this backend's streaming endpoint is POST (it needs a
 * request body). Instead: `fetch()` + `response.body` (a `ReadableStream`)
 * + `TextDecoder`, with the frame/event parsing in sseParser.ts.
 */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export interface StreamChatMessageParams {
  sessionId: string;
  message: string;
  signal: AbortSignal;
  onEvent: (event: SSEEvent) => void;
}

export async function streamChatMessage({ sessionId, message, signal, onEvent }: StreamChatMessageParams): Promise<void> {
  const response = await fetch(`${getApiBaseUrl()}/api/sessions/${encodeURIComponent(sessionId)}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });

  if (!response.ok) {
    if (import.meta.env.DEV) {
      console.debug(`[sse] stream request failed with status ${response.status}`);
    }
    throw new ApiError("The assistant could not be reached. Please try again.", response.status);
  }
  if (!response.body) {
    throw new ApiError("Streaming is not supported in this environment.", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;

    const chunkText = decoder.decode(value, { stream: true });
    const { frames, rest } = appendAndSplitFrames(buffer, chunkText);
    buffer = rest;

    for (const frame of frames) {
      const rawData = extractDataPayload(frame);
      if (rawData === null) continue;
      const event = parseSSEEvent(rawData);
      if (event !== null) onEvent(event);
    }
  }

  // A frame left in `buffer` with no trailing blank line at true stream
  // end is an incomplete/truncated frame, not a valid one — deliberately
  // discarded rather than guessed at. The caller (runBackendChat.ts)
  // treats "stream ended without a run.completed event" as an implicit
  // failure, so an incomplete final frame can never leave the UI stuck.
}
