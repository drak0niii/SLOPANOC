import { describe, expect, it, vi } from "vitest";
import type { SSEEvent } from "./types";

const streamChatMessage = vi.fn();
vi.mock("./streamChat", async () => {
  const actual = await vi.importActual<typeof import("./streamChat")>("./streamChat");
  return { ...actual, streamChatMessage: (...args: unknown[]) => streamChatMessage(...args) };
});

import { runBackendChat, type BackendChatHandlers } from "./runBackendChat";

function makeHandlers(): BackendChatHandlers & Record<keyof BackendChatHandlers, ReturnType<typeof vi.fn>> {
  return {
    onRunStarted: vi.fn(),
    onStatus: vi.fn(),
    onStatusClear: vi.fn(),
    onDelta: vi.fn(),
    onCompleted: vi.fn(),
    onActionPending: vi.fn(),
    onSelectionPending: vi.fn(),
    onError: vi.fn(),
    onRunCompleted: vi.fn(),
    onTraceStep: vi.fn(),
  };
}

function envelope(type: string, data: unknown): SSEEvent {
  return {
    type,
    session_id: "s1",
    run_id: "r1",
    sequence: 1,
    timestamp: "2026-01-01T00:00:00Z",
    data,
  } as SSEEvent;
}

describe("runBackendChat", () => {
  it("dispatches each event to its matching handler, in order", async () => {
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("run.started", {}));
      onEvent(envelope("status", { stage: "processing", label: "Processing your request" }));
      onEvent(envelope("status.clear", {}));
      onEvent(envelope("message.delta", { text: "Hi" }));
      onEvent(envelope("message.completed", { content: "Hi" }));
      onEvent(envelope("run.completed", { outcome: "ok" }));
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onRunStarted).toHaveBeenCalledWith("r1");
    expect(handlers.onStatus).toHaveBeenCalledWith("processing", "Processing your request");
    expect(handlers.onStatusClear).toHaveBeenCalledOnce();
    expect(handlers.onDelta).toHaveBeenCalledWith("Hi");
    expect(handlers.onCompleted).toHaveBeenCalledWith("Hi", undefined, undefined);
    expect(handlers.onRunCompleted).toHaveBeenCalledWith("ok");
    expect(handlers.onError).not.toHaveBeenCalled();
  });

  it("passes a structured source through to onCompleted when present (pre-4H UX/provenance milestone)", async () => {
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
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("message.completed", { content: "Here is the summary.", source }));
      onEvent(envelope("run.completed", { outcome: "ok" }));
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onCompleted).toHaveBeenCalledWith("Here is the summary.", source, undefined);
  });

  it("passes structured knowledge_sources through to onCompleted when present (Phase 5.1J correction pass)", async () => {
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
      content: "For the Aurora Relay verification, confirm that the relay checksum is exactly 7319 and the status indicator is GREEN.",
    };
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("message.completed", { content: "The checksum is 7319.", knowledge_sources: [knowledgeSource] }));
      onEvent(envelope("run.completed", { outcome: "ok" }));
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onCompleted).toHaveBeenCalledWith("The checksum is 7319.", undefined, [knowledgeSource]);
  });

  it("dispatches trace.step events to onTraceStep, verbatim", async () => {
    const step = { step_id: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" };
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("trace.step", step));
      onEvent(envelope("run.completed", { outcome: "ok" }));
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onTraceStep).toHaveBeenCalledWith(step);
  });

  it("handles the zero-delta single-shot fallback sequence correctly", async () => {
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("run.started", {}));
      onEvent(envelope("status", { stage: "processing", label: "Processing your request" }));
      onEvent(envelope("status.clear", {}));
      onEvent(envelope("message.completed", { content: "The full answer at once." }));
      onEvent(envelope("run.completed", { outcome: "ok" }));
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onDelta).not.toHaveBeenCalled();
    expect(handlers.onCompleted).toHaveBeenCalledWith("The full answer at once.", undefined, undefined);
    expect(handlers.onRunCompleted).toHaveBeenCalledWith("ok");
  });

  it("synthesizes an error + run-completed(error) if the stream ends without run.completed", async () => {
    streamChatMessage.mockImplementation(async ({ onEvent }: { onEvent: (e: SSEEvent) => void }) => {
      onEvent(envelope("run.started", {}));
      // stream just ends here, no run.completed
    });

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onError).toHaveBeenCalledOnce();
    expect(handlers.onRunCompleted).toHaveBeenCalledWith("error");
  });

  it("on a transport error, calls onError with a safe message and onRunCompleted(error)", async () => {
    streamChatMessage.mockRejectedValue(new Error("raw network failure detail, should never leak"));

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onError).toHaveBeenCalledOnce();
    const [info] = handlers.onError.mock.calls[0];
    expect(info.message).not.toContain("raw network failure detail");
    expect(handlers.onRunCompleted).toHaveBeenCalledWith("error");
  });

  it("silently swallows an AbortError — never calls onError or onRunCompleted", async () => {
    streamChatMessage.mockRejectedValue(new DOMException("Aborted", "AbortError"));

    const handlers = makeHandlers();
    await runBackendChat("s1", "hello", handlers, new AbortController().signal);

    expect(handlers.onError).not.toHaveBeenCalled();
    expect(handlers.onRunCompleted).not.toHaveBeenCalled();
  });
});
