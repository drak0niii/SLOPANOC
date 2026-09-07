import { KNOWN_SSE_EVENT_TYPES, type SSEEvent } from "./types";

/**
 * A minimal, dependency-free SSE frame/event parser for `fetch()`-based
 * streaming (see streamChat.ts — browser `EventSource` is GET-only and
 * cannot be used against this backend's POST streaming endpoint).
 *
 * A `ReadableStream` chunk boundary has NO relationship to an SSE event
 * boundary — one chunk may contain zero, one, or several complete frames,
 * and a frame may be split across many chunks. Every function here is
 * pure and independently testable; `streamChat.ts` owns the actual
 * fetch/reader loop.
 */

/**
 * Normalizes CRLF to LF across the combined (already-buffered + new)
 * text, then splits on the blank-line SSE frame boundary. Returns
 * complete frames plus whatever incomplete tail should be carried into
 * the next call as `buffer`. Safe to call repeatedly — re-normalizing an
 * already-normalized buffer is a no-op, and a CRLF blank line split
 * across two chunks (e.g. one ending in "\r", the next starting "\n\r\n")
 * is only ever normalized AFTER concatenation, never before, so it's
 * never missed.
 */
export function appendAndSplitFrames(buffer: string, chunk: string): { frames: string[]; rest: string } {
  const combined = (buffer + chunk).replace(/\r\n/g, "\n");
  const parts = combined.split("\n\n");
  const rest = parts.pop() ?? "";
  return { frames: parts, rest };
}

/**
 * Extracts the JSON payload from one SSE frame's `data:` line(s). Per the
 * SSE spec, multiple `data:` lines within one frame are joined with "\n";
 * exactly one leading space after the colon (if present) is stripped, not
 * all leading whitespace. Non-`data:` lines (e.g. an `event:` line, which
 * this backend also sends but which is redundant with the JSON payload's
 * own `type` field — see streamChat.ts) are ignored. Returns `null` for a
 * frame with no `data:` line at all (e.g. a stray blank/comment frame).
 */
export function extractDataPayload(frame: string): string | null {
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue;
    let value = line.slice("data:".length);
    if (value.startsWith(" ")) value = value.slice(1);
    dataLines.push(value);
  }
  return dataLines.length > 0 ? dataLines.join("\n") : null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number";
}

function devWarn(message: string): void {
  if (import.meta.env.DEV) console.debug(`[sse] ${message}`);
}

/**
 * Parses one frame's JSON `data:` payload into a typed `SSEEvent`, with
 * lightweight runtime validation (no schema-validation dependency exists
 * in this project). Never throws: malformed JSON, an invalid envelope
 * shape, an unknown/future `type`, or a `data` shape that doesn't match
 * its `type` all resolve to `null` (with a dev-console-only diagnostic
 * via `onWarn`/`console.debug`) rather than crashing the stream consumer
 * or the app.
 */
export function parseSSEEvent(rawData: string, onWarn: (message: string) => void = devWarn): SSEEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawData);
  } catch {
    onWarn("received malformed SSE payload (invalid JSON)");
    return null;
  }

  if (typeof parsed !== "object" || parsed === null) {
    onWarn("received malformed SSE payload (not an object)");
    return null;
  }
  const obj = parsed as Record<string, unknown>;

  if (
    !isString(obj.type) ||
    !isString(obj.session_id) ||
    !isString(obj.run_id) ||
    !isNumber(obj.sequence) ||
    !isString(obj.timestamp) ||
    typeof obj.data !== "object" ||
    obj.data === null
  ) {
    onWarn("received SSE payload with an invalid envelope shape");
    return null;
  }

  if (!(KNOWN_SSE_EVENT_TYPES as readonly string[]).includes(obj.type)) {
    // A future backend event type this build doesn't know about yet —
    // safely ignored, never surfaced to the UI or thrown.
    onWarn(`ignoring unknown event type: ${obj.type}`);
    return null;
  }

  const data = obj.data as Record<string, unknown>;
  switch (obj.type as SSEEvent["type"]) {
    case "run.started":
    case "status.clear":
      break;
    case "status":
      if (!isString(data.stage) || !isString(data.label)) {
        onWarn("malformed status event");
        return null;
      }
      break;
    case "message.delta":
      if (!isString(data.text)) {
        onWarn("malformed message.delta event");
        return null;
      }
      break;
    case "message.completed":
      if (!isString(data.content)) {
        onWarn("malformed message.completed event");
        return null;
      }
      // `source` is optional (pre-4H UX/provenance milestone) — only
      // validated when present, and only shallowly: this event's own
      // `content` is the field every consumer needs; a malformed/future-
      // shaped `source` is dropped rather than discarding the whole
      // message, matching this parser's general "never crash the stream
      // over one unexpected field" posture.
      if (data.source !== undefined) {
        const source = data.source as Record<string, unknown>;
        if (
          typeof source !== "object" ||
          source === null ||
          source.source_type !== "teams" ||
          !isString(source.source_id) ||
          !isString(source.label) ||
          !Array.isArray(source.contributors) ||
          !Array.isArray(source.evidence)
        ) {
          onWarn("dropping malformed source on message.completed event");
          delete (data as { source?: unknown }).source;
        }
      }
      // Phase 5.1J correction pass (Part C): `knowledge_sources` is a
      // SEPARATE, additive array — validated independently of `source`
      // above, and just as tolerant of a malformed/future shape (drop the
      // one bad entry, or the whole array, rather than the message).
      if (data.knowledge_sources !== undefined) {
        if (!Array.isArray(data.knowledge_sources)) {
          onWarn("dropping malformed knowledge_sources on message.completed event");
          delete (data as { knowledge_sources?: unknown }).knowledge_sources;
        } else {
          const valid = (data.knowledge_sources as unknown[]).filter((entry): boolean => {
            if (typeof entry !== "object" || entry === null) return false;
            const ref = entry as Record<string, unknown>;
            return (
              ref.source_type === "knowledge" &&
              isString(ref.source_id) &&
              isString(ref.knowledge_id) &&
              isString(ref.version_label) &&
              isString(ref.section_id) &&
              isString(ref.title) &&
              isString(ref.content)
            );
          });
          if (valid.length !== (data.knowledge_sources as unknown[]).length) {
            onWarn("dropping malformed knowledge_sources entries on message.completed event");
          }
          (data as { knowledge_sources?: unknown }).knowledge_sources = valid;
        }
      }
      break;
    case "action.pending":
      if (!isString(data.proposal_id) || !isString(data.operation) || !isString(data.status)) {
        onWarn("malformed action.pending event");
        return null;
      }
      break;
    case "selection.pending":
      if (
        !isString(data.selection_id) ||
        !isString(data.kind) ||
        !isString(data.status) ||
        !isString(data.requested_value) ||
        !Array.isArray(data.options) ||
        !data.options.every(
          (opt) =>
            typeof opt === "object" &&
            opt !== null &&
            isString((opt as Record<string, unknown>).option_id) &&
            isString((opt as Record<string, unknown>).label),
        )
      ) {
        onWarn("malformed selection.pending event");
        return null;
      }
      break;
    case "error":
      if (!isString(data.code) || !isString(data.message)) {
        onWarn("malformed error event");
        return null;
      }
      break;
    case "run.completed":
      if (data.outcome !== "ok" && data.outcome !== "error") {
        onWarn("malformed run.completed event");
        return null;
      }
      break;
    case "trace.step":
      if (
        !isString(data.step_id) ||
        !isString(data.category) ||
        !isString(data.label) ||
        (data.status !== "completed" && data.status !== "warning" && data.status !== "failed")
      ) {
        onWarn("malformed trace.step event");
        return null;
      }
      break;
  }

  return {
    type: obj.type,
    session_id: obj.session_id,
    run_id: obj.run_id,
    sequence: obj.sequence,
    timestamp: obj.timestamp,
    data,
  } as SSEEvent;
}
