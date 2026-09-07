import { describe, expect, it, vi } from "vitest";
import { appendAndSplitFrames, extractDataPayload, parseSSEEvent } from "./sseParser";
import { KNOWN_SSE_EVENT_TYPES } from "./types";

const BASE_ENVELOPE = { session_id: "s1", run_id: "r1", sequence: 1, timestamp: "2026-01-01T00:00:00Z" };

describe("appendAndSplitFrames", () => {
  it("returns one complete frame from a single chunk", () => {
    const { frames, rest } = appendAndSplitFrames("", "event: status\ndata: {}\n\n");
    expect(frames).toEqual(["event: status\ndata: {}"]);
    expect(rest).toBe("");
  });

  it("handles multiple events in one chunk", () => {
    const chunk = "event: a\ndata: {\"x\":1}\n\nevent: b\ndata: {\"y\":2}\n\n";
    const { frames, rest } = appendAndSplitFrames("", chunk);
    expect(frames).toHaveLength(2);
    expect(rest).toBe("");
  });

  it("handles a frame split across two chunks", () => {
    const first = appendAndSplitFrames("", "event: status\ndata: {\"a\":");
    expect(first.frames).toEqual([]);
    const second = appendAndSplitFrames(first.rest, "1}\n\n");
    expect(second.frames).toEqual(['event: status\ndata: {"a":1}']);
  });

  it("normalizes CRLF line endings", () => {
    const { frames } = appendAndSplitFrames("", "event: status\r\ndata: {}\r\n\r\n");
    expect(frames).toEqual(["event: status\ndata: {}"]);
  });

  it("handles a CRLF blank-line boundary split exactly at the CR/LF seam", () => {
    const first = appendAndSplitFrames("", "event: status\r\ndata: {}\r");
    const second = appendAndSplitFrames(first.rest, "\n\r\n");
    expect(second.frames).toEqual(["event: status\ndata: {}"]);
  });

  it("carries an incomplete trailing frame as rest", () => {
    const { frames, rest } = appendAndSplitFrames("", "event: status\ndata: {}\n\nevent: partial\ndata: {\"x\"");
    expect(frames).toEqual(["event: status\ndata: {}"]);
    expect(rest).toBe("event: partial\ndata: {\"x\"");
  });
});

describe("extractDataPayload", () => {
  it("extracts a single data: line, stripping exactly one leading space", () => {
    expect(extractDataPayload("event: status\ndata: {\"a\":1}")).toBe('{"a":1}');
  });

  it("joins multiple data: lines with a newline", () => {
    expect(extractDataPayload("data: line1\ndata: line2")).toBe("line1\nline2");
  });

  it("returns null for a frame with no data: line", () => {
    expect(extractDataPayload(": this is a comment\nevent: ping")).toBeNull();
  });

  it("preserves extra leading whitespace beyond the single required space", () => {
    expect(extractDataPayload("data:  indented")).toBe(" indented");
  });
});

describe("parseSSEEvent", () => {
  it("parses each of the 9 known event types", () => {
    const onWarn = vi.fn();
    const cases: Array<[string, unknown]> = [
      ["run.started", {}],
      ["status", { stage: "processing", label: "Processing your request", presentation: "replace" }],
      ["status.clear", {}],
      ["message.delta", { text: "Hello" }],
      ["message.completed", { content: "Hello world" }],
      [
        "action.pending",
        {
          proposal_id: "p1",
          operation: "teams.sendMessage",
          status: "pending",
          summary: null,
          title: null,
          members: [],
          chat_id: null,
          message: null,
          expires_at: "2026-01-01T00:05:00Z",
          expires_in_seconds: 300,
          expires_in_minutes: 5,
        },
      ],
      [
        "selection.pending",
        {
          selection_id: "sel1",
          kind: "teams.chat",
          status: "pending",
          requested_value: "Project Falcon Room",
          options: [{ option_id: "opt1", label: "Project Falcon Room Test" }],
        },
      ],
      ["error", { code: "run_failure", message: "The assistant could not complete this request." }],
      ["run.completed", { outcome: "ok" }],
      [
        "trace.step",
        { step_id: "step1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
      ],
    ];

    for (const [type, data] of cases) {
      const raw = JSON.stringify({ ...BASE_ENVELOPE, type, data });
      const event = parseSSEEvent(raw, onWarn);
      expect(event).not.toBeNull();
      expect(event?.type).toBe(type);
      expect(event?.data).toEqual(data);
    }
    expect(onWarn).not.toHaveBeenCalled();
  });

  it("confirms all 10 known types are covered by this test file's cases", () => {
    expect(KNOWN_SSE_EVENT_TYPES).toHaveLength(10);
  });

  it("parses a trace.step event with optional safe_metadata present", () => {
    const onWarn = vi.fn();
    const data = {
      step_id: "step1",
      category: "evidence",
      label: "Reviewed 5 retrieved messages",
      status: "completed",
      safe_metadata: { message_count: 5 },
    };
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "trace.step", data });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual(data);
    expect(onWarn).not.toHaveBeenCalled();
  });

  it("returns null for a trace.step event missing a required field", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "trace.step",
      data: { step_id: "step1", category: "teams", status: "completed" }, // missing label
    });
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed trace.step"));
  });

  it("returns null for a trace.step event with an invalid status value", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "trace.step",
      data: { step_id: "step1", category: "teams", label: "x", status: "in_progress" },
    });
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed trace.step"));
  });

  it("parses a message.completed event with a structured Teams source present (pre-4H UX/provenance milestone)", () => {
    const onWarn = vi.fn();
    const source = {
      source_id: "src1",
      source_type: "teams",
      label: "Teams conversation",
      title: "Ops Bridge",
      message_count: 29,
      period_start: "2026-08-26T09:00:00Z",
      period_end: "2026-09-01T09:00:00Z",
      contributors: ["Alex", "Priya"],
      evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z" }],
    };
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "message.completed", data: { content: "Summary.", source } });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual({ content: "Summary.", source });
    expect(onWarn).not.toHaveBeenCalled();
  });

  it("parses a message.completed event with no source the same as before (fully backward-compatible)", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "message.completed", data: { content: "Hi." } });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual({ content: "Hi." });
  });

  it("drops a malformed source but keeps the rest of the message.completed event", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "message.completed",
      data: { content: "Summary.", source: { source_type: "teams" /* missing required fields */ } },
    });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual({ content: "Summary." });
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed source"));
  });

  it("parses a message.completed event with structured knowledge_sources present (Phase 5.1J correction pass)", () => {
    const onWarn = vi.fn();
    const knowledgeSource = {
      source_id: "ks1",
      source_type: "knowledge",
      label: "Governed knowledge",
      knowledge_id: "aurora-relay-verification",
      version_label: "v1",
      section_id: "aurora-relay-verification:v1:s0",
      title: "Aurora Relay Verification Procedure",
      document_type: "technical_instruction",
      source_system: "manual_e2e_fixture",
      evidence_source_id: "doc-1",
      source_display_name: "Aurora Relay Governed Test Procedure",
      section_heading: "Verification",
      source_locator: "test-fixture:verification",
      content: "Confirm the checksum is 7319 and the status is GREEN.",
    };
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "message.completed",
      data: { content: "The checksum is 7319.", knowledge_sources: [knowledgeSource] },
    });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual({ content: "The checksum is 7319.", knowledge_sources: [knowledgeSource] });
    expect(onWarn).not.toHaveBeenCalled();
  });

  it("drops only the malformed entries within knowledge_sources, keeping valid ones", () => {
    const onWarn = vi.fn();
    const valid = {
      source_id: "ks1",
      source_type: "knowledge",
      knowledge_id: "k1",
      version_label: "v1",
      section_id: "k1:v1:s0",
      title: "Guide",
      content: "Text",
    };
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "message.completed",
      data: { content: "Answer.", knowledge_sources: [valid, { source_type: "knowledge" /* missing fields */ }] },
    });
    const event = parseSSEEvent(raw, onWarn);
    expect(event).not.toBeNull();
    expect(event?.data).toEqual({ content: "Answer.", knowledge_sources: [valid] });
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed knowledge_sources"));
  });

  it("never claims source_uri appears anywhere in a parsed knowledge_sources entry", () => {
    const onWarn = vi.fn();
    const knowledgeSource = {
      source_id: "ks1",
      source_type: "knowledge",
      knowledge_id: "k1",
      version_label: "v1",
      section_id: "k1:v1:s0",
      title: "Guide",
      content: "Text",
    };
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "message.completed",
      data: { content: "Answer.", knowledge_sources: [knowledgeSource] },
    });
    const event = parseSSEEvent(raw, onWarn);
    expect(JSON.stringify(event?.data)).not.toContain("source_uri");
  });

  it("returns null for a malformed selection.pending event", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "selection.pending",
      data: { selection_id: "sel1", kind: "teams.chat", status: "pending", requested_value: "X" }, // missing options
    });
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed selection.pending"));
  });

  it("returns null when a selection.pending option is missing a required field", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({
      ...BASE_ENVELOPE,
      type: "selection.pending",
      data: {
        selection_id: "sel1",
        kind: "teams.chat",
        status: "pending",
        requested_value: "X",
        options: [{ option_id: "opt1" }], // missing label
      },
    });
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledWith(expect.stringContaining("malformed selection.pending"));
  });

  it("returns null and warns on invalid JSON", () => {
    const onWarn = vi.fn();
    expect(parseSSEEvent("{not json", onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledOnce();
  });

  it("returns null for a JSON value that isn't an object", () => {
    const onWarn = vi.fn();
    expect(parseSSEEvent("42", onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledOnce();
  });

  it("returns null and warns for a missing envelope field", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({ type: "status", session_id: "s1", data: {} }); // no run_id/sequence/timestamp
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledOnce();
  });

  it("safely ignores an unknown/future event type without throwing", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "future.event", data: { anything: true } });
    expect(() => parseSSEEvent(raw, onWarn)).not.toThrow();
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
  });

  it("returns null for a status event missing a required data field", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "status", data: { stage: "processing" } }); // no label
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledOnce();
  });

  it("returns null for a run.completed event with an invalid outcome value", () => {
    const onWarn = vi.fn();
    const raw = JSON.stringify({ ...BASE_ENVELOPE, type: "run.completed", data: { outcome: "maybe" } });
    expect(parseSSEEvent(raw, onWarn)).toBeNull();
    expect(onWarn).toHaveBeenCalledOnce();
  });

  it("uses a default dev-only warn handler when none is supplied, without throwing", () => {
    expect(() => parseSSEEvent("{not json")).not.toThrow();
  });
});
