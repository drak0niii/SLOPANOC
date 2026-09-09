import { describe, expect, it, vi } from "vitest";
import { initialState, reducer, type AppState } from "./AppState";
import type {
  KnowledgeSourceReferenceDTO,
  PendingActionDTO,
  PendingSelectionDTO,
  SourceReferenceDTO,
  TraceStepDTO,
} from "../api/types";

const CHAT_ID = "chat-1";
const USER_MSG_ID = "msg-user-1";
const ASSISTANT_MSG_ID = "msg-assistant-1";
const RUN_TOKEN = "run-1";

/** Seeds a general-scope chat with an in-flight real backend run, exactly
 * as `sendMessage`'s SEND_MESSAGE dispatch would (see AppState.tsx). */
function seedRunningChat(): AppState {
  return reducer(
    { ...initialState, workspaceScope: { type: "general" } },
    {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: true,
        userMessageId: USER_MSG_ID,
        assistantMessageId: ASSISTANT_MSG_ID,
        text: "What happened in the selected Teams conversation today?",
        attachments: [],
        sources: [],
        timestamp: 1000,
        runToken: RUN_TOKEN,
      },
    },
  );
}

function pendingAction(overrides: Partial<PendingActionDTO> = {}): PendingActionDTO {
  return {
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
    target_display_name: null,
    ...overrides,
  };
}

describe("reducer — SEND_MESSAGE (Phase 4F seeding)", () => {
  it("seeds chat.run when runToken is present", () => {
    const state = seedRunningChat();
    expect(state.chats[CHAT_ID].run).toEqual({
      runToken: RUN_TOKEN,
      assistantMessageId: ASSISTANT_MSG_ID,
      currentActivity: null,
      activityTrail: [],
      runStartedAt: 1000,
    });
  });

  it("does not touch chat.run for the existing mock paths (no runToken)", () => {
    const state = reducer(initialState, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: true,
        userMessageId: USER_MSG_ID,
        assistantMessageId: ASSISTANT_MSG_ID,
        text: "hello",
        attachments: [],
        sources: [],
        timestamp: 1000,
      },
    });
    expect(state.chats[CHAT_ID].run).toBeUndefined();
  });
});

describe("reducer — BACKEND_STATUS_UPDATE / BACKEND_STATUS_CLEAR (replace, not append)", () => {
  it("overwrites currentActivity rather than accumulating it", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "teams_context", label: "Retrieving recent messages", activityKind: null },
    });
    expect(state.chats[CHAT_ID].run?.currentActivity).toEqual({
      stage: "teams_context",
      label: "Retrieving recent messages",
      activityKind: null,
    });

    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "evidence_processing", label: "Reviewing 84 messages", activityKind: null },
    });
    // The SAME slot, not a second entry — there is nowhere in the state
    // shape a second activity could even be appended to.
    expect(state.chats[CHAT_ID].run?.currentActivity).toEqual({
      stage: "evidence_processing",
      label: "Reviewing 84 messages",
      activityKind: null,
    });
  });

  it("displays whatever label the backend sends verbatim, with no frontend-hardcoded mapping", () => {
    // A stage value this frontend has never seen before still renders —
    // proves there's no closed switch/lookup table gating on `stage`.
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "a_future_stage_value", label: "Checking the current fault evidence", activityKind: null },
    });
    expect(state.chats[CHAT_ID].run?.currentActivity?.label).toBe("Checking the current fault evidence");
  });

  it("BACKEND_STATUS_CLEAR clears currentActivity", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "processing", label: "Processing your request", activityKind: null },
    });
    state = reducer(state, { type: "BACKEND_STATUS_CLEAR", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    expect(state.chats[CHAT_ID].run?.currentActivity).toBeNull();
  });
});

describe("reducer — activity trail (Phase 2, Runtime Activity Truthfulness)", () => {
  function status(stage: string, label: string, activityKind: string | null = null) {
    return { type: "BACKEND_STATUS_UPDATE" as const, payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage, label, activityKind } };
  }

  it("(F) accumulates DISTINCT statuses chronologically", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, status("evidence_processing", "Reviewing retrieved knowledge", "knowledge_search_succeeded"));
    state = reducer(
      state,
      status("knowledge_retrieval", "Validating supporting evidence", "knowledge_evidence_selection_started"),
    );
    expect(state.chats[CHAT_ID].run?.activityTrail.map((e) => e.label)).toEqual([
      "Searching governed knowledge",
      "Reviewing retrieved knowledge",
      "Validating supporting evidence",
    ]);
  });

  it("(G) a repeated, IDENTICAL status is collapsed, never appended as a duplicate trail entry", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    expect(state.chats[CHAT_ID].run?.activityTrail).toHaveLength(1);
  });

  it("(H) the trail is bounded — oldest entries drop once the cap is exceeded", () => {
    let state = seedRunningChat();
    for (let i = 0; i < 20; i++) {
      state = reducer(state, status(`stage-${i}`, `Activity ${i}`, null));
    }
    const trail = state.chats[CHAT_ID].run?.activityTrail ?? [];
    expect(trail.length).toBeLessThanOrEqual(10);
    // The MOST RECENT entry always survives -- oldest are dropped first.
    expect(trail[trail.length - 1].label).toBe("Activity 19");
  });

  it("(I) a new run (SEND_MESSAGE) resets the trail — no stale entries from a prior run leak forward", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    expect(state.chats[CHAT_ID].run?.activityTrail).toHaveLength(1);

    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "next turn",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    expect(state.chats[CHAT_ID].run?.activityTrail).toEqual([]);
  });

  it("(J) cancelling a run (RUN_STOPPED) clears the current activity and the run itself — no stale 'working' state lingers", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    expect(state.chats[CHAT_ID].run).toBeUndefined();
  });

  it("(K) a run ending in error clears the current activity and the run itself", () => {
    let state = seedRunningChat();
    state = reducer(state, status("teams_context", "Retrieving Teams messages", "teams_messages_retrieval_started"));
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "error" },
    });
    expect(state.chats[CHAT_ID].run).toBeUndefined();
  });

  it("(L/M) two different chats' activity trails never cross-contaminate", () => {
    const OTHER_CHAT_ID = "chat-2";
    let state = seedRunningChat();
    // Seed a second chat with its own independent run.
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: OTHER_CHAT_ID,
        isNewChat: true,
        userMessageId: "msg-user-other",
        assistantMessageId: "msg-assistant-other",
        text: "second chat",
        attachments: [],
        sources: [],
        timestamp: 3000,
        runToken: "run-other",
      },
    });

    state = reducer(state, status("teams_context", "Finding the Teams conversation", "teams_chat_discovery_started"));
    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: {
        chatId: OTHER_CHAT_ID,
        runToken: "run-other",
        stage: "knowledge_retrieval",
        label: "Searching governed knowledge",
        activityKind: "knowledge_search_started",
      },
    });

    const teamsTrail = state.chats[CHAT_ID].run?.activityTrail.map((e) => e.label) ?? [];
    const knowledgeTrail = state.chats[OTHER_CHAT_ID].run?.activityTrail.map((e) => e.label) ?? [];
    expect(teamsTrail).toEqual(["Finding the Teams conversation"]);
    expect(knowledgeTrail).toEqual(["Searching governed knowledge"]);
    expect(teamsTrail).not.toContain("Searching governed knowledge");
    expect(knowledgeTrail).not.toContain("Finding the Teams conversation");
  });

  it("(N) a generic turn ('hello') that only ever sends the initial processing status never accumulates any capability-specific trail entry", () => {
    let state = seedRunningChat();
    state = reducer(state, status("processing", "Processing your request", null));
    const trail = state.chats[CHAT_ID].run?.activityTrail.map((e) => e.label) ?? [];
    expect(trail).toEqual(["Processing your request"]);
    expect(trail.some((l) => /teams|knowledge|case/i.test(l))).toBe(false);
  });
});

describe("reducer — UI PRESENTATION CORRECTION: activityTrail merges into completed RunTraceRecord.steps", () => {
  function status(stage: string, label: string, activityKind: string | null = null) {
    return { type: "BACKEND_STATUS_UPDATE" as const, payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage, label, activityKind } };
  }

  it("BACKEND_RUN_COMPLETED folds the run's own trail ahead of any genuine trace.step milestones, in order, exactly once", () => {
    let state = seedRunningChat();
    state = reducer(state, status("processing", "Processing your request", null));
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, status("evidence_processing", "Reviewing retrieved knowledge", "knowledge_search_succeeded"));
    state = reducer(
      state,
      status("knowledge_retrieval", "Validating supporting evidence", "knowledge_evidence_selection_started"),
    );
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ label: "Generated the response" }),
      },
    });
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const mergedLabels = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label);
    // Reproduces the accepted example exactly: the live trail's own
    // entries, chronologically first, followed by the genuine
    // (pre-existing, backend-recorded) trace-step milestone.
    expect(mergedLabels).toEqual([
      "Processing your request",
      "Searching governed knowledge",
      "Reviewing retrieved knowledge",
      "Validating supporting evidence",
      "Generated the response",
    ]);
  });

  it("every activity-derived step is marked 'completed' — never fabricates a warning/failed status", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const steps = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps ?? [];
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ label: "Searching governed knowledge", status: "completed" });
  });

  it("RUN_STOPPED performs the same merge for a user-initiated stop", () => {
    let state = seedRunningChat();
    state = reducer(state, status("teams_context", "Finding the Teams conversation", "teams_chat_discovery_started"));
    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    const steps = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps ?? [];
    expect(steps.map((s) => s.label)).toEqual(["Finding the Teams conversation"]);
    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].outcome).toBe("stopped");
  });

  it("an empty trail (e.g. an instantly-completed run) leaves the existing trace steps completely untouched", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ label: "Generated the response" }),
      },
    });
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label)).toEqual([
      "Generated the response",
    ]);
  });

  it("no duplicates: an activity-trail label identical to an already-recorded trace-step label is not repeated", () => {
    let state = seedRunningChat();
    state = reducer(state, status("response", "Generated the response", null));
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ label: "Generated the response" }),
      },
    });
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const labels = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label) ?? [];
    expect(labels.filter((l) => l === "Generated the response")).toHaveLength(1);
  });

  it("never deduplicates two genuinely DIFFERENT labels merely because they look similar", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, status("evidence_processing", "Reviewing retrieved knowledge", "knowledge_search_succeeded"));
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const labels = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label) ?? [];
    expect(labels).toEqual(["Searching governed knowledge", "Reviewing retrieved knowledge"]);
  });

  it("a new run's freshly-reset (empty) trail never re-merges a PRIOR run's already-frozen steps a second time", () => {
    let state = seedRunningChat();
    state = reducer(state, status("knowledge_retrieval", "Searching governed knowledge", "knowledge_search_started"));
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    // A second, unrelated run targeting a DIFFERENT message must not
    // touch the first run's already-frozen trace record.
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "next turn",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: "run-2", outcome: "ok" },
    });
    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label)).toEqual([
      "Searching governed knowledge",
    ]);
    expect(next.chats[CHAT_ID].runTraces?.["msg-assistant-2"].steps).toEqual([]);
  });
});

describe("reducer — BACKEND_MESSAGE_DELTA", () => {
  it("accumulates delta text onto the target message and flips status to streaming", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "The" },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: " evidence" },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: " indicates..." },
    });

    const message = state.messages[ASSISTANT_MSG_ID];
    expect(message.text).toBe("The evidence indicates...");
    expect(message.status).toBe("streaming");
  });

  it("clears currentActivity defensively on the first delta, even without an explicit status.clear", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "processing", label: "Processing your request", activityKind: null },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "Hi" },
    });
    expect(state.chats[CHAT_ID].run?.currentActivity).toBeNull();
  });
});

describe("reducer — BACKEND_MESSAGE_COMPLETED (reconciliation)", () => {
  it("overwrites accumulated delta text with the authoritative content, never appends/duplicates", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "The evi" },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "The evidence indicates a config change." },
    });

    const message = state.messages[ASSISTANT_MSG_ID];
    expect(message.text).toBe("The evidence indicates a config change.");
    expect(message.status).toBe("complete");
  });
});

// --- Structured Teams source/provenance (pre-4H UX/provenance milestone) ---

function sourceReference(overrides: Partial<SourceReferenceDTO> = {}): SourceReferenceDTO {
  return {
    source_id: "src1",
    source_type: "teams",
    label: "Teams conversation",
    title: "Ops Bridge",
    message_count: 29,
    period_start: "2026-08-26T09:00:00Z",
    period_end: "2026-09-01T09:00:00Z",
    contributors: ["Alex", "Priya"],
    evidence: [{ author: "Alex", sent_at: "2026-08-26T09:00:00Z", snippet: "We should escalate this now." }],
    ...overrides,
  };
}

// --- Phase 5.1J correction pass: governed-knowledge source/provenance -----

function knowledgeSourceReference(overrides: Partial<KnowledgeSourceReferenceDTO> = {}): KnowledgeSourceReferenceDTO {
  return {
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
    ...overrides,
  };
}

describe("reducer — BACKEND_MESSAGE_COMPLETED attaches a structured source", () => {
  it("stores the source under chat.sources, keyed by the owning assistant message id", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "Here is the summary.",
        source: sourceReference(),
      },
    });

    expect(state.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID]).toEqual(sourceReference());
  });

  it("does not create a sources entry when no source is present (e.g. a plain 'hello')", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "Hi!" },
    });

    expect(state.chats[CHAT_ID].sources).toBeUndefined();
  });

  it("is a no-op for a mismatched/stale runToken, even when a source is present", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: "some-other-run",
        messageId: ASSISTANT_MSG_ID,
        content: "Here is the summary.",
        source: sourceReference(),
      },
    });
    expect(next).toBe(state);
  });

  it("multiple turns each get their own distinct, correctly-owned source (no cross-assignment)", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "First summary.",
        source: sourceReference({ source_id: "src-a", title: "Ops Bridge" }),
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "now summarize a different chat",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: "run-2",
        messageId: secondAssistantId,
        content: "Second summary.",
        source: sourceReference({ source_id: "src-b", title: "Knowledge Management Daily Sync up" }),
      },
    });

    expect(state.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID].title).toBe("Ops Bridge");
    expect(state.chats[CHAT_ID].sources?.[secondAssistantId].title).toBe("Knowledge Management Daily Sync up");
    expect(Object.keys(state.chats[CHAT_ID].sources ?? {})).toHaveLength(2);
  });
});

describe("reducer — EDIT_MESSAGE discards a discarded turn's source, mirrors runTraces/actionCards cleanup", () => {
  it("removes the source owned by a message that no longer exists after the edit", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "Here is the summary.",
        source: sourceReference(),
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    expect(state.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID]).toBeDefined();

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID, // the message BEFORE the assistant turn that owns the source
        text: "a different opening prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });

    expect(next.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID]).toBeUndefined();
    expect(Object.keys(next.chats[CHAT_ID].sources ?? {})).toHaveLength(0);
  });

  it("leaves an EARLIER, surviving message's source completely untouched by editing a LATER message", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "Here is the summary.",
        source: sourceReference(),
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondUserId = "msg-user-2";
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: secondUserId,
        assistantMessageId: secondAssistantId,
        text: "a follow-up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: secondUserId,
        text: "actually, something else",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    expect(next.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID]).toEqual(sourceReference());
  });
});

describe("reducer — BACKEND_MESSAGE_COMPLETED attaches structured knowledge_sources (Phase 5.1J correction pass)", () => {
  it("stores knowledge_sources under chat.knowledgeSources, keyed by the owning assistant message id", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "The checksum is 7319.",
        knowledgeSources: [knowledgeSourceReference()],
      },
    });

    expect(state.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toEqual([knowledgeSourceReference()]);
  });

  it("does not create a knowledgeSources entry when knowledge_search was called but nothing was selected", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "No governed knowledge selected." },
    });

    expect(state.chats[CHAT_ID].knowledgeSources).toBeUndefined();
  });

  it("does not create a knowledgeSources entry for an empty array", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "No governed knowledge selected.",
        knowledgeSources: [],
      },
    });

    expect(state.chats[CHAT_ID].knowledgeSources).toBeUndefined();
  });

  it("stores BOTH a Teams source and knowledge_sources on the same message (combined-answer turn)", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "Combined answer.",
        source: sourceReference(),
        knowledgeSources: [knowledgeSourceReference()],
      },
    });

    expect(state.chats[CHAT_ID].sources?.[ASSISTANT_MSG_ID]).toEqual(sourceReference());
    expect(state.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toEqual([knowledgeSourceReference()]);
  });

  it("stores multiple distinct selected KM references, preserving order", () => {
    const first = knowledgeSourceReference({ source_id: "ks1", section_id: "s1" });
    const second = knowledgeSourceReference({ source_id: "ks2", section_id: "s2" });
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "Combined answer.",
        knowledgeSources: [first, second],
      },
    });

    expect(state.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toEqual([first, second]);
  });
});

describe("reducer — EDIT_MESSAGE discards a discarded turn's knowledgeSources, mirrors sources cleanup (Part C8)", () => {
  it("removes the knowledgeSources owned by a message that no longer exists after the edit", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "The checksum is 7319.",
        knowledgeSources: [knowledgeSourceReference()],
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    expect(state.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toBeDefined();

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID,
        text: "a different opening prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });

    expect(next.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toBeUndefined();
    expect(Object.keys(next.chats[CHAT_ID].knowledgeSources ?? {})).toHaveLength(0);
  });

  it("leaves an EARLIER, surviving message's knowledgeSources completely untouched by editing a LATER message", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        content: "The checksum is 7319.",
        knowledgeSources: [knowledgeSourceReference()],
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondUserId = "msg-user-2";
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: secondUserId,
        assistantMessageId: secondAssistantId,
        text: "a follow-up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: secondUserId,
        text: "actually, something else",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    expect(next.chats[CHAT_ID].knowledgeSources?.[ASSISTANT_MSG_ID]).toEqual([knowledgeSourceReference()]);
  });
});

describe("reducer — stale runToken is a no-op", () => {
  it("drops BACKEND_STATUS_UPDATE for a mismatched runToken", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_STATUS_UPDATE",
      payload: { chatId: CHAT_ID, runToken: "some-other-run", stage: "processing", label: "stale", activityKind: null },
    });
    expect(next).toBe(state); // unchanged reference — a true no-op
  });

  it("drops BACKEND_MESSAGE_DELTA for a mismatched runToken (never mutates the message)", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: "some-other-run", messageId: ASSISTANT_MSG_ID, textDelta: "stale" },
    });
    expect(next.messages[ASSISTANT_MSG_ID].text).toBe("");
  });

  it("drops BACKEND_RUN_COMPLETED for a mismatched runToken (run stays active)", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: "some-other-run", outcome: "ok" },
    });
    expect(next.chats[CHAT_ID].run).toBeDefined();
  });
});

describe("reducer — BACKEND_ACTION_PENDING", () => {
  it("populates chat.pendingAction, isolated from the message object", () => {
    const state = seedRunningChat();
    const action = pendingAction();
    const next = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, action },
    });
    expect(next.chats[CHAT_ID].pendingAction).toEqual(action);
    expect(next.messages[ASSISTANT_MSG_ID].actionProposalId).toBeUndefined();
  });
});

describe("reducer — BACKEND_RUN_COMPLETED", () => {
  it("clears chat.run but preserves an existing chat.pendingAction (adjustment: never cleared by a new run)", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, action: pendingAction() },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "done" },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    expect(state.chats[CHAT_ID].run).toBeUndefined();
    expect(state.chats[CHAT_ID].pendingAction).toBeDefined();
  });

  it("a NEW run does not clear a prior pendingAction when it starts (SEND_MESSAGE)", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, action: pendingAction() },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    // A second message in the same chat starts a new run.
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "follow up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    expect(state.chats[CHAT_ID].pendingAction).toBeDefined();
  });

  it("force-flips a still-pending/streaming message to error if outcome is error with no prior explicit error event", () => {
    const state = seedRunningChat(); // message is still "pending"
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "error" },
    });
    expect(next.messages[ASSISTANT_MSG_ID].status).toBe("error");
    expect(next.messages[ASSISTANT_MSG_ID].errorMessage).toBeTruthy();
  });

  it("does not overwrite an already-set errorMessage from an explicit BACKEND_RUN_ERROR", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_RUN_ERROR",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, message: "The assistant could not complete this request." },
    });
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "error" },
    });
    expect(state.messages[ASSISTANT_MSG_ID].errorMessage).toBe("The assistant could not complete this request.");
  });
});

describe("reducer — BACKEND_RUN_ERROR", () => {
  it("sets status/errorMessage without clobbering already-streamed partial text", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "Partial answer" },
    });
    state = reducer(state, {
      type: "BACKEND_RUN_ERROR",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, message: "Connection lost." },
    });

    const message = state.messages[ASSISTANT_MSG_ID];
    expect(message.text).toBe("Partial answer");
    expect(message.status).toBe("error");
    expect(message.errorMessage).toBe("Connection lost.");
  });
});

// --- Phase 4G: real approval card (approve -> execute lifecycle) -----------
// Phase 4G hardening pass: the card itself lives in per-message
// `chat.actionCards`, keyed by the assistant message that emitted it —
// never a single global per-chat slot. `chat.pendingAction`/
// `pendingActionMessageId` remain as "authoritative current backend
// state" only (used to resolve which record an approve/reject/execute
// response should update), never as the render source.

const PROPOSAL_ID = "p1";

function record(state: AppState, messageId: string) {
  return state.chats[CHAT_ID].actionCards?.[messageId];
}

/** Seeds a chat with a running turn AND a live pendingAction, exactly as
 * `beginBackendRun`'s onActionPending handler would dispatch it — the
 * precondition every APPROVAL_* reducer case needs (`withMatchingProposal`
 * only applies an update when `chat.pendingAction.proposal_id` matches). */
function seedChatWithPendingAction(overrides: Partial<PendingActionDTO> = {}): AppState {
  const state = seedRunningChat();
  return reducer(state, {
    type: "BACKEND_ACTION_PENDING",
    payload: {
      chatId: CHAT_ID,
      runToken: RUN_TOKEN,
      messageId: ASSISTANT_MSG_ID,
      action: pendingAction({ proposal_id: PROPOSAL_ID, ...overrides }),
    },
  });
}

describe("reducer — BACKEND_ACTION_PENDING (Phase 4G additions)", () => {
  it("sets pendingActionMessageId alongside pendingAction", () => {
    const state = seedChatWithPendingAction();
    expect(state.chats[CHAT_ID].pendingActionMessageId).toBe(ASSISTANT_MSG_ID);
  });

  it("creates a new, expanded actionCards entry keyed by the message id", () => {
    const state = seedChatWithPendingAction();
    expect(record(state, ASSISTANT_MSG_ID)).toEqual({
      proposalId: PROPOSAL_ID,
      pendingAction: pendingAction({ proposal_id: PROPOSAL_ID }),
      approvalCard: undefined,
      collapsed: false,
    });
  });

  it("a NEW proposal on a DIFFERENT message adds its own record without touching the old message's record", () => {
    let state = seedChatWithPendingAction();
    state = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });
    expect(record(state, ASSISTANT_MSG_ID)?.approvalCard).toBeDefined();

    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: "msg-assistant-2",
        action: pendingAction({ proposal_id: "p2" }),
      },
    });

    // The old message's own record is untouched — still exists, still
    // carries whatever lifecycle state it had (never deleted, never
    // reset just because a newer proposal exists elsewhere).
    expect(record(state, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase: "approving" });
    expect(record(state, ASSISTANT_MSG_ID)?.proposalId).toBe(PROPOSAL_ID);
    // The new message gets its own, freshly-expanded record.
    expect(record(state, "msg-assistant-2")).toEqual({
      proposalId: "p2",
      pendingAction: pendingAction({ proposal_id: "p2" }),
      approvalCard: undefined,
      collapsed: false,
    });
    expect(state.chats[CHAT_ID].pendingActionMessageId).toBe("msg-assistant-2");
  });

  it("leaves an in-flight approvalCard untouched when the SAME proposal_id is redelivered to the SAME message", () => {
    let state = seedChatWithPendingAction();
    state = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });
    const cardBefore = record(state, ASSISTANT_MSG_ID)?.approvalCard;

    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        action: pendingAction({ proposal_id: PROPOSAL_ID }), // identical proposal_id
      },
    });
    expect(record(state, ASSISTANT_MSG_ID)?.approvalCard).toEqual(cardBefore);
  });
});

describe("reducer — SEND_MESSAGE auto-collapses existing action cards (Phase 4G hardening pass)", () => {
  it("collapses an existing expanded card when a new user message is sent, without moving or deleting it", () => {
    let state = seedChatWithPendingAction();
    expect(record(state, ASSISTANT_MSG_ID)?.collapsed).toBe(false);

    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "follow up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const collapsedRecord = record(state, ASSISTANT_MSG_ID);
    expect(collapsedRecord?.collapsed).toBe(true);
    // Still the same proposal, still attached to the same original
    // message — never moved, never removed.
    expect(collapsedRecord?.proposalId).toBe(PROPOSAL_ID);
  });

  it("never touches actionCards at all for a chat that has none yet", () => {
    const state = seedRunningChat(); // no BACKEND_ACTION_PENDING dispatched
    const next = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "hello again",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    expect(next.chats[CHAT_ID].actionCards).toBeUndefined();
  });
});

describe("reducer — TOGGLE_ACTION_CARD_COLLAPSED", () => {
  it("flips a card's collapsed flag in place, leaving everything else about the record untouched", () => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, {
      type: "TOGGLE_ACTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID },
    });
    expect(record(next, ASSISTANT_MSG_ID)?.collapsed).toBe(true);
    expect(record(next, ASSISTANT_MSG_ID)?.proposalId).toBe(PROPOSAL_ID);

    const toggledBack = reducer(next, {
      type: "TOGGLE_ACTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID },
    });
    expect(record(toggledBack, ASSISTANT_MSG_ID)?.collapsed).toBe(false);
  });

  it("is a no-op for a message with no action card", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "TOGGLE_ACTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: "msg-with-no-card" },
    });
    expect(next).toBe(state);
  });
});

describe("reducer — APPROVAL_APPROVE_STARTED / APPROVAL_APPROVE_SUCCEEDED", () => {
  it("APPROVAL_APPROVE_STARTED sets the owning record's approvalCard to phase 'approving'", () => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase: "approving" });
  });

  it("APPROVAL_APPROVE_SUCCEEDED syncs both chat.pendingAction and the record's own pendingAction, and moves to 'executing'", () => {
    let state = seedChatWithPendingAction();
    state = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });

    const authoritative = pendingAction({ proposal_id: PROPOSAL_ID, status: "approved" });
    const next = reducer(state, {
      type: "APPROVAL_APPROVE_SUCCEEDED",
      payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID, pendingAction: authoritative },
    });

    expect(next.chats[CHAT_ID].pendingAction).toEqual(authoritative);
    expect(record(next, ASSISTANT_MSG_ID)?.pendingAction).toEqual(authoritative);
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase: "executing" });
  });

  it("a stale request (proposal superseded mid-flight) is a no-op", () => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, {
      type: "APPROVAL_APPROVE_STARTED",
      payload: { chatId: CHAT_ID, proposalId: "some-other-proposal" },
    });
    expect(next).toBe(state); // unchanged reference — a true no-op
  });
});

describe("reducer — APPROVAL_EXECUTE_SUCCEEDED", () => {
  it("syncs pendingAction (now consumed) and moves the owning record to 'completed' with the executedAction", () => {
    let state = seedChatWithPendingAction();
    state = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });
    state = reducer(state, {
      type: "APPROVAL_APPROVE_SUCCEEDED",
      payload: {
        chatId: CHAT_ID,
        proposalId: PROPOSAL_ID,
        pendingAction: pendingAction({ proposal_id: PROPOSAL_ID, status: "approved" }),
      },
    });

    const consumed = pendingAction({ proposal_id: PROPOSAL_ID, status: "consumed" });
    const executedAction = { chatId: "c1", title: null, webUrl: "https://teams/x" };
    const next = reducer(state, {
      type: "APPROVAL_EXECUTE_SUCCEEDED",
      payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID, pendingAction: consumed, executedAction },
    });

    expect(next.chats[CHAT_ID].pendingAction).toEqual(consumed);
    expect(record(next, ASSISTANT_MSG_ID)?.pendingAction).toEqual(consumed);
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase: "completed", executedAction });
  });
});

describe("reducer — APPROVAL_REJECT_STARTED / APPROVAL_REJECT_SUCCEEDED", () => {
  it("APPROVAL_REJECT_STARTED sets the owning record's approvalCard to phase 'rejecting'", () => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase: "rejecting" });
  });

  it("APPROVAL_REJECT_SUCCEEDED syncs pendingAction and clears the record's approvalCard (rejected is derived from pendingAction.status)", () => {
    let state = seedChatWithPendingAction();
    state = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID } });

    const rejected = pendingAction({ proposal_id: PROPOSAL_ID, status: "rejected" });
    const next = reducer(state, {
      type: "APPROVAL_REJECT_SUCCEEDED",
      payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID, pendingAction: rejected },
    });

    expect(next.chats[CHAT_ID].pendingAction).toEqual(rejected);
    expect(record(next, ASSISTANT_MSG_ID)?.pendingAction).toEqual(rejected);
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toBeUndefined();
  });
});

describe("reducer — APPROVAL_REQUEST_FAILED", () => {
  it.each(["expired", "failed", "unconfirmed"] as const)("sets the owning record's approvalCard to phase %s with the given message", (phase) => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, {
      type: "APPROVAL_REQUEST_FAILED",
      payload: { chatId: CHAT_ID, proposalId: PROPOSAL_ID, phase, message: "exact backend message" },
    });
    expect(record(next, ASSISTANT_MSG_ID)?.approvalCard).toEqual({ proposalId: PROPOSAL_ID, phase, message: "exact backend message" });
  });

  it("a stale request (proposal superseded mid-flight) is a no-op — never mutates a different proposal's card", () => {
    const state = seedChatWithPendingAction();
    const next = reducer(state, {
      type: "APPROVAL_REQUEST_FAILED",
      payload: { chatId: CHAT_ID, proposalId: "some-other-proposal", phase: "failed", message: "stale" },
    });
    expect(next).toBe(state);
  });
});

describe("reducer — multiple historical action cards coexist, each attached to its own message", () => {
  it("two proposals on two different messages keep two independent, terminal records", () => {
    let state = seedChatWithPendingAction({ proposal_id: "p1" });
    state = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: "p1" } });
    state = reducer(state, {
      type: "APPROVAL_REJECT_SUCCEEDED",
      payload: { chatId: CHAT_ID, proposalId: "p1", pendingAction: pendingAction({ proposal_id: "p1", status: "rejected" }) },
    });

    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: "msg-assistant-2",
        action: pendingAction({ proposal_id: "p2" }),
      },
    });
    state = reducer(state, { type: "APPROVAL_APPROVE_STARTED", payload: { chatId: CHAT_ID, proposalId: "p2" } });
    state = reducer(state, {
      type: "APPROVAL_APPROVE_SUCCEEDED",
      payload: { chatId: CHAT_ID, proposalId: "p2", pendingAction: pendingAction({ proposal_id: "p2", status: "approved" }) },
    });

    // Message 1's card: permanently rejected, still attached to message 1.
    expect(record(state, ASSISTANT_MSG_ID)?.proposalId).toBe("p1");
    expect(record(state, ASSISTANT_MSG_ID)?.pendingAction.status).toBe("rejected");
    // Message 2's card: independently executing, attached to message 2.
    expect(record(state, "msg-assistant-2")?.proposalId).toBe("p2");
    expect(record(state, "msg-assistant-2")?.approvalCard).toEqual({ proposalId: "p2", phase: "executing" });
  });
});

// --- CRITICAL regression: a resolved historical card must never reappear ---
// under a later, unrelated turn (Phase 4G hardening pass, second round).
// Root cause traced and fixed: BACKEND_ACTION_PENDING previously always
// upserted a fresh record at the INCOMING messageId, even when the same
// proposal_id already owned a record elsewhere -- so any event carrying an
// already-known proposal_id (however that happens) produced a second,
// duplicate card. The fix locates the EXISTING owner by proposal_id first
// and updates that record in place; only a genuinely new proposal_id ever
// creates a new record.

describe("reducer — CRITICAL regression: rejected card must not reappear under an unrelated later turn", () => {
  it("matches the exact reported scenario: reject a proposal, then an unrelated turn with NO action.pending produces no second card", () => {
    let state = seedChatWithPendingAction({ proposal_id: "p1" });
    state = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: "p1" } });
    state = reducer(state, {
      type: "APPROVAL_REJECT_SUCCEEDED",
      payload: {
        chatId: CHAT_ID,
        proposalId: "p1",
        pendingAction: pendingAction({ proposal_id: "p1", status: "rejected" }),
      },
    });
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });

    // Turn 2: an unrelated user message ("can you also write code?").
    const secondAssistantId = "msg-assistant-2";
    const secondRunToken = "run-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "can you also write code?",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: secondRunToken,
      },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: {
        chatId: CHAT_ID,
        runToken: secondRunToken,
        messageId: secondAssistantId,
        content: "Sure, here's some code...",
      },
    });
    // NO BACKEND_ACTION_PENDING dispatched for turn 2 -- the assistant produced no action.

    // The rejected card still exists, still attached to the ORIGINAL message, now collapsed.
    expect(record(state, ASSISTANT_MSG_ID)?.proposalId).toBe("p1");
    expect(record(state, ASSISTANT_MSG_ID)?.pendingAction.status).toBe("rejected");
    expect(record(state, ASSISTANT_MSG_ID)?.collapsed).toBe(true);

    // Turn 2's own message has no action card at all.
    expect(record(state, secondAssistantId)).toBeUndefined();

    // Exactly one record for proposal p1 across the whole chat.
    const allRecords = Object.values(state.chats[CHAT_ID].actionCards ?? {});
    expect(allRecords.filter((r) => r.proposalId === "p1")).toHaveLength(1);
  });

  it("the traced root cause: a re-reported SAME already-resolved proposal_id on a NEW message updates the ORIGINAL card in place, never cloning a second one", () => {
    let state = seedChatWithPendingAction({ proposal_id: "p1" });
    state = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: "p1" } });
    state = reducer(state, {
      type: "APPROVAL_REJECT_SUCCEEDED",
      payload: {
        chatId: CHAT_ID,
        proposalId: "p1",
        pendingAction: pendingAction({ proposal_id: "p1", status: "rejected" }),
      },
    });
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });

    const secondAssistantId = "msg-assistant-2";
    const secondRunToken = "run-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "can you also write code?",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: secondRunToken,
      },
    });
    // The SAME (already rejected) proposal_id is re-surfaced, this time
    // attached to the NEW assistant message.
    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: secondRunToken,
        messageId: secondAssistantId,
        action: pendingAction({ proposal_id: "p1", status: "rejected" }),
      },
    });

    // Still exactly one record for p1, still attached to the ORIGINAL
    // message -- never cloned, never moved to the new one.
    const allRecords = Object.entries(state.chats[CHAT_ID].actionCards ?? {});
    const p1Owners = allRecords.filter(([, r]) => r.proposalId === "p1").map(([messageId]) => messageId);
    expect(p1Owners).toEqual([ASSISTANT_MSG_ID]);
    expect(record(state, secondAssistantId)).toBeUndefined();
  });

  it("expanding the historical rejected card still works, and it remains collapsible independent of the new turn", () => {
    let state = seedChatWithPendingAction({ proposal_id: "p1" });
    state = reducer(state, { type: "APPROVAL_REJECT_STARTED", payload: { chatId: CHAT_ID, proposalId: "p1" } });
    state = reducer(state, {
      type: "APPROVAL_REJECT_SUCCEEDED",
      payload: {
        chatId: CHAT_ID,
        proposalId: "p1",
        pendingAction: pendingAction({ proposal_id: "p1", status: "rejected" }),
      },
    });
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "can you also write code?",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    expect(record(state, ASSISTANT_MSG_ID)?.collapsed).toBe(true);

    const expanded = reducer(state, {
      type: "TOGGLE_ACTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID },
    });
    expect(record(expanded, ASSISTANT_MSG_ID)?.collapsed).toBe(false);
    expect(record(expanded, ASSISTANT_MSG_ID)?.pendingAction.status).toBe("rejected");
  });
});

describe("reducer — multiple proposals across turns, no cross-assignment (Phase 4G hardening pass)", () => {
  it("Turn A's proposal stays on Turn A; Turn B (no proposal) has no card; Turn C's proposal is only on Turn C", () => {
    let state = seedChatWithPendingAction({ proposal_id: "p1" }); // Turn A: proposal 1, on ASSISTANT_MSG_ID

    const turnBAssistantId = "msg-assistant-B";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-B",
        assistantMessageId: turnBAssistantId,
        text: "unrelated question",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-B",
      },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: "run-B", messageId: turnBAssistantId, content: "Sure, here's the answer." },
    });
    // No BACKEND_ACTION_PENDING for Turn B -- it produced no action.

    const turnCAssistantId = "msg-assistant-C";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-C",
        assistantMessageId: turnCAssistantId,
        text: "create a new chat",
        attachments: [],
        sources: [],
        timestamp: 3000,
        runToken: "run-C",
      },
    });
    state = reducer(state, {
      type: "BACKEND_ACTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: "run-C",
        messageId: turnCAssistantId,
        action: pendingAction({ proposal_id: "p2", operation: "teams.createChat" }),
      },
    });

    expect(record(state, ASSISTANT_MSG_ID)?.proposalId).toBe("p1");
    expect(record(state, turnBAssistantId)).toBeUndefined();
    expect(record(state, turnCAssistantId)?.proposalId).toBe("p2");

    // Exactly two records total -- one per proposal, no duplication.
    const allRecords = Object.values(state.chats[CHAT_ID].actionCards ?? {});
    expect(allRecords).toHaveLength(2);
  });
});

// --- EDIT_MESSAGE: restored backend-chat editing + stable action-card ------
// ownership across the resulting truncation (Phase 4G hardening pass).

describe("reducer — EDIT_MESSAGE restores backend-chat editing and preserves stable action-card ownership", () => {
  it("seeds chat.run with the given runToken for a backend-sourced edit (mirrors SEND_MESSAGE)", () => {
    const state = seedRunningChat();
    const editRunToken = "edit-run-1";
    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID,
        text: "edited prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
        runToken: editRunToken,
      },
    });
    expect(next.chats[CHAT_ID].run).toEqual({
      runToken: editRunToken,
      assistantMessageId: "msg-assistant-edited",
      currentActivity: null,
      activityTrail: [],
      runStartedAt: expect.any(Number),
    });
  });

  it("does not touch chat.run when no runToken is given (mock-path edit, unaffected)", () => {
    let state = seedRunningChat();
    // Realistic sequencing: the run must have finished before the user
    // could edit an earlier message, and a mock-path chat never had
    // chat.run set in the first place -- either way, chat.run is not
    // present here going into the edit.
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID,
        text: "edited prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });
    expect(next.chats[CHAT_ID].run).toBeUndefined();
  });

  it("editing the message that precedes an action-owning assistant turn discards that turn's action card along with it", () => {
    const state = seedChatWithPendingAction({ proposal_id: "p1" }); // owned by ASSISTANT_MSG_ID
    expect(record(state, ASSISTANT_MSG_ID)).toBeDefined();

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID, // the message BEFORE the assistant turn that owns the card
        text: "a different opening prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });

    // The old assistant message (and the card it owned) is gone entirely
    // -- never left as orphaned state pointing at a deleted message.
    expect(next.chats[CHAT_ID].actionCards?.[ASSISTANT_MSG_ID]).toBeUndefined();
    expect(Object.keys(next.chats[CHAT_ID].actionCards ?? {})).toHaveLength(0);
    expect(next.chats[CHAT_ID].pendingAction).toBeNull();
    expect(next.chats[CHAT_ID].pendingActionMessageId).toBeUndefined();
    // The new placeholder assistant message has no card of its own yet.
    expect(next.chats[CHAT_ID].actionCards?.["msg-assistant-edited"]).toBeUndefined();
  });

  it("editing a LATER, unrelated message leaves an EARLIER action card's ownership completely untouched", () => {
    // Turn 1: user1 -> assistant1 (owns a card, proposal p1).
    let state = seedChatWithPendingAction({ proposal_id: "p1" });
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });

    // Turn 2: an unrelated follow-up, no action.
    const secondUserId = "msg-user-2";
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: secondUserId,
        assistantMessageId: secondAssistantId,
        text: "can you also write code?",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: "run-2", messageId: secondAssistantId, content: "Sure." },
    });

    // Now edit the SECOND user message -- discards only what came after it;
    // turn 1's message/card, which precedes the edit point, is untouched.
    const editedNext = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: secondUserId,
        text: "actually, can you write a poem?",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    expect(record(editedNext, ASSISTANT_MSG_ID)?.proposalId).toBe("p1");
    expect(editedNext.chats[CHAT_ID].pendingAction?.proposal_id).toBe("p1");
    expect(editedNext.chats[CHAT_ID].pendingActionMessageId).toBe(ASSISTANT_MSG_ID);
    // The discarded second-turn assistant message never had a card, and
    // still doesn't under its replacement.
    expect(record(editedNext, secondAssistantId)).toBeUndefined();
    expect(record(editedNext, "msg-assistant-2-edited")).toBeUndefined();
    // Exactly one action card total, still owned by the original message.
    expect(Object.keys(editedNext.chats[CHAT_ID].actionCards ?? {})).toEqual([ASSISTANT_MSG_ID]);
  });

  it("does not duplicate an action card when editing a message unrelated to it", () => {
    const state = seedChatWithPendingAction({ proposal_id: "p1" });
    const next = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "unrelated",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    const edited = reducer(next, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: "msg-user-2",
        text: "still unrelated, edited",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    const allRecords = Object.values(edited.chats[CHAT_ID].actionCards ?? {});
    expect(allRecords.filter((r) => r.proposalId === "p1")).toHaveLength(1);
  });
});

// --- Interaction-capability extension: Teams chat-name selection -----------

const SELECTION_ID = "sel1";

function pendingSelection(overrides: Partial<PendingSelectionDTO> = {}): PendingSelectionDTO {
  return {
    selection_id: SELECTION_ID,
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

function selectionRecord(state: AppState, messageId: string) {
  return state.chats[CHAT_ID].selectionCards?.[messageId];
}

/** Mirrors `seedChatWithPendingAction` — the precondition every
 * SELECTION_* reducer case needs (`withMatchingSelection` only applies
 * an update when `chat.pendingSelection.selection_id` matches). */
function seedChatWithPendingSelection(overrides: Partial<PendingSelectionDTO> = {}): AppState {
  const state = seedRunningChat();
  return reducer(state, {
    type: "BACKEND_SELECTION_PENDING",
    payload: {
      chatId: CHAT_ID,
      runToken: RUN_TOKEN,
      messageId: ASSISTANT_MSG_ID,
      selection: pendingSelection(overrides),
    },
  });
}

describe("reducer — BACKEND_SELECTION_PENDING", () => {
  it("sets pendingSelection/pendingSelectionMessageId/pendingSelectionRunToken", () => {
    const state = seedChatWithPendingSelection();
    expect(state.chats[CHAT_ID].pendingSelection).toEqual(pendingSelection());
    expect(state.chats[CHAT_ID].pendingSelectionMessageId).toBe(ASSISTANT_MSG_ID);
    expect(state.chats[CHAT_ID].pendingSelectionRunToken).toBe(RUN_TOKEN);
  });

  it("creates a new, expanded selectionCards entry keyed by the message id", () => {
    const state = seedChatWithPendingSelection();
    expect(selectionRecord(state, ASSISTANT_MSG_ID)).toEqual({
      selectionId: SELECTION_ID,
      pendingSelection: pendingSelection(),
      selectionCard: undefined,
      collapsed: false,
    });
  });

  it("a stale runToken is a no-op (mirrors BACKEND_ACTION_PENDING's guard)", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_SELECTION_PENDING",
      payload: {
        chatId: CHAT_ID,
        runToken: "wrong-token",
        messageId: ASSISTANT_MSG_ID,
        selection: pendingSelection(),
      },
    });
    expect(next).toBe(state);
  });
});

describe("reducer — BEGIN_READ_RESUME (hardening pass: no synthetic user message)", () => {
  it("appends only a new assistant placeholder message, never a paired user message", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, {
      type: "SELECTION_CHOOSE_SUCCEEDED",
      payload: { chatId: CHAT_ID, selectionId: SELECTION_ID, selectedLabel: "Project Falcon Room Test", pendingAction: null },
    });
    const userMessageCountBefore = Object.values(state.messages).filter((m) => m.role === "user").length;
    const messageIdCountBefore = state.chats[CHAT_ID].messageIds.length;

    const resumeAssistantId = "msg-resume-assistant";
    const next = reducer(state, {
      type: "BEGIN_READ_RESUME",
      payload: { chatId: CHAT_ID, assistantMessageId: resumeAssistantId, runToken: "resume-run-1", timestamp: 5000 },
    });

    expect(next.messages[resumeAssistantId]).toEqual({
      id: resumeAssistantId,
      chatId: CHAT_ID,
      role: "assistant",
      text: "",
      status: "pending",
      createdAt: 5000,
    });
    expect(next.chats[CHAT_ID].messageIds).toHaveLength(messageIdCountBefore + 1);
    expect(next.chats[CHAT_ID].messageIds.at(-1)).toBe(resumeAssistantId);
    const userMessageCountAfter = Object.values(next.messages).filter((m) => m.role === "user").length;
    expect(userMessageCountAfter).toBe(userMessageCountBefore); // no new user message anywhere
  });

  it("seeds chat.run so the normal elapsed-timer/status machinery works for the resumed turn", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "BEGIN_READ_RESUME",
      payload: { chatId: CHAT_ID, assistantMessageId: "msg-resume", runToken: "resume-run-1", timestamp: 5000 },
    });
    expect(next.chats[CHAT_ID].run).toEqual({
      runToken: "resume-run-1",
      assistantMessageId: "msg-resume",
      currentActivity: null,
      activityTrail: [],
      runStartedAt: 5000,
    });
  });

  it("collapses existing selection cards, mirroring SEND_MESSAGE's own new-turn collapse behavior", () => {
    const state = seedChatWithPendingSelection();
    expect(selectionRecord(state, ASSISTANT_MSG_ID)?.collapsed).toBe(false);

    const next = reducer(state, {
      type: "BEGIN_READ_RESUME",
      payload: { chatId: CHAT_ID, assistantMessageId: "msg-resume", runToken: "resume-run-1", timestamp: 5000 },
    });

    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.collapsed).toBe(true);
    // The card's own content — including which selection/candidates it
    // represents — is completely untouched; only presentation collapses.
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.pendingSelection).toEqual(pendingSelection());
  });

  it("is a no-op for an unknown chatId", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "BEGIN_READ_RESUME",
      payload: { chatId: "does-not-exist", assistantMessageId: "msg-resume", runToken: "r", timestamp: 5000 },
    });
    expect(next).toBe(state);
  });
});

describe("reducer — SEND_MESSAGE auto-collapses existing selection cards", () => {
  it("collapses a selection card when a new message is sent, without touching its content", () => {
    let state = seedChatWithPendingSelection();
    expect(selectionRecord(state, ASSISTANT_MSG_ID)?.collapsed).toBe(false);

    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "unrelated follow-up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const record = selectionRecord(state, ASSISTANT_MSG_ID);
    expect(record?.collapsed).toBe(true);
    expect(record?.pendingSelection).toEqual(pendingSelection());
  });
});

describe("reducer — TOGGLE_SELECTION_CARD_COLLAPSED", () => {
  it("flips only the targeted record's collapsed flag", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "TOGGLE_SELECTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID },
    });
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.collapsed).toBe(true);
    const back = reducer(next, {
      type: "TOGGLE_SELECTION_CARD_COLLAPSED",
      payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID },
    });
    expect(selectionRecord(back, ASSISTANT_MSG_ID)?.collapsed).toBe(false);
  });
});

describe("reducer — SELECTION_CHOOSE_STARTED / SELECTION_CHOOSE_SUCCEEDED", () => {
  it("STARTED sets the owning record's selectionCard to phase 'choosing'", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "SELECTION_CHOOSE_STARTED",
      payload: { chatId: CHAT_ID, selectionId: SELECTION_ID },
    });
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard).toEqual({
      selectionId: SELECTION_ID,
      phase: "choosing",
    });
  });

  it("STARTED is a no-op for a selectionId that is no longer the chat's current one", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "SELECTION_CHOOSE_STARTED",
      payload: { chatId: CHAT_ID, selectionId: "stale-selection" },
    });
    expect(next).toBe(state);
  });

  it("SUCCEEDED (read-kind, no pendingAction) marks resolved with the selected label and clears pendingSelection", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "SELECTION_CHOOSE_STARTED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });
    const next = reducer(state, {
      type: "SELECTION_CHOOSE_SUCCEEDED",
      payload: { chatId: CHAT_ID, selectionId: SELECTION_ID, selectedLabel: "Project Falcon Room Test", pendingAction: null },
    });

    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard).toEqual({
      selectionId: SELECTION_ID,
      phase: "resolved",
      selectedLabel: "Project Falcon Room Test",
    });
    expect(next.chats[CHAT_ID].pendingSelection).toBeNull();
    expect(next.chats[CHAT_ID].pendingSelectionMessageId).toBeUndefined();
    // No proposal was created for a read-kind resolution.
    expect(next.chats[CHAT_ID].pendingAction).toBeUndefined();
    expect(next.chats[CHAT_ID].actionCards).toBeUndefined();
  });

  it("SUCCEEDED (write-kind, pendingAction present) anchors a new ActionProposal card to the selection's owning message", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "SELECTION_CHOOSE_STARTED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });
    const action = pendingAction({ proposal_id: "p-new", chat_id: "chat-real-1", message: "hello" });
    const next = reducer(state, {
      type: "SELECTION_CHOOSE_SUCCEEDED",
      payload: { chatId: CHAT_ID, selectionId: SELECTION_ID, selectedLabel: "Project Falcon Room Test", pendingAction: action },
    });

    expect(next.chats[CHAT_ID].pendingAction).toEqual(action);
    expect(next.chats[CHAT_ID].pendingActionMessageId).toBe(ASSISTANT_MSG_ID);
    expect(record(next, ASSISTANT_MSG_ID)).toEqual({
      proposalId: "p-new",
      pendingAction: action,
      approvalCard: undefined,
      collapsed: false,
    });
    // The selection itself is no longer current, but its own card still
    // reflects that it resolved successfully.
    expect(next.chats[CHAT_ID].pendingSelection).toBeNull();
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard?.phase).toBe("resolved");
  });

  it("SUCCEEDED is a no-op for a stale selectionId", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, {
      type: "SELECTION_CHOOSE_SUCCEEDED",
      payload: { chatId: CHAT_ID, selectionId: "stale-selection", selectedLabel: "X", pendingAction: null },
    });
    expect(next).toBe(state);
  });
});

describe("reducer — SELECTION_SKIP_STARTED / SELECTION_SKIP_SUCCEEDED", () => {
  it("STARTED sets phase 'skipping'", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, { type: "SELECTION_SKIP_STARTED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard?.phase).toBe("skipping");
  });

  it("SUCCEEDED marks skipped, creates no proposal, clears pendingSelection", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "SELECTION_SKIP_STARTED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });
    const next = reducer(state, { type: "SELECTION_SKIP_SUCCEEDED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });

    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard).toEqual({ selectionId: SELECTION_ID, phase: "skipped" });
    expect(next.chats[CHAT_ID].pendingSelection).toBeNull();
    expect(next.chats[CHAT_ID].pendingAction).toBeUndefined();
    expect(next.chats[CHAT_ID].actionCards).toBeUndefined();
  });
});

describe("reducer — SELECTION_REQUEST_FAILED", () => {
  it("sets phase 'failed' with the given message, without clearing pendingSelection (still retryable)", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "SELECTION_CHOOSE_STARTED", payload: { chatId: CHAT_ID, selectionId: SELECTION_ID } });
    const next = reducer(state, {
      type: "SELECTION_REQUEST_FAILED",
      payload: { chatId: CHAT_ID, selectionId: SELECTION_ID, message: "That option does not belong to this selection." },
    });
    expect(selectionRecord(next, ASSISTANT_MSG_ID)?.selectionCard).toEqual({
      selectionId: SELECTION_ID,
      phase: "failed",
      message: "That option does not belong to this selection.",
    });
    expect(next.chats[CHAT_ID].pendingSelection).toEqual(pendingSelection());
  });
});

describe("reducer — BACKEND_RUN_COMPLETED marks a non-reaffirmed selection stale", () => {
  it("clears pendingSelection when a LATER run completes without a new BACKEND_SELECTION_PENDING for it", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    // Selection was reaffirmed by the SAME run that created it -- still current.
    expect(state.chats[CHAT_ID].pendingSelection).toEqual(pendingSelection());

    // A later, unrelated turn starts and completes without ever
    // re-affirming this selection_id (e.g. an exact-match resolution
    // elsewhere superseded it server-side).
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: "msg-assistant-2",
        text: "Project Falcon Room Test, summarize it",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: "run-2", outcome: "ok" } });

    expect(state.chats[CHAT_ID].pendingSelection).toBeNull();
    expect(state.chats[CHAT_ID].pendingSelectionMessageId).toBeUndefined();
    expect(state.chats[CHAT_ID].pendingSelectionRunToken).toBeUndefined();
    // The frozen historical card itself is untouched -- still there, still
    // shows its original options; only the chat-level "current" pointer
    // is cleared (see selectionCard.ts's `deriveSelectionCardView`, which
    // renders it "stale" once pendingSelection no longer matches).
    expect(selectionRecord(state, ASSISTANT_MSG_ID)?.pendingSelection).toEqual(pendingSelection());
  });

  it("does NOT clear pendingSelection when the SAME run both creates and completes it", () => {
    const state = seedChatWithPendingSelection();
    const next = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    expect(next.chats[CHAT_ID].pendingSelection).toEqual(pendingSelection());
  });
});

describe("reducer — EDIT_MESSAGE discards an orphaned selection card, mirroring action-card cleanup", () => {
  it("editing the message that precedes a selection-owning assistant turn discards that turn's selection card along with it", () => {
    const state = seedChatWithPendingSelection();
    expect(selectionRecord(state, ASSISTANT_MSG_ID)).toBeDefined();

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID,
        text: "a different opening prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });

    expect(next.chats[CHAT_ID].selectionCards?.[ASSISTANT_MSG_ID]).toBeUndefined();
    expect(Object.keys(next.chats[CHAT_ID].selectionCards ?? {})).toHaveLength(0);
    expect(next.chats[CHAT_ID].pendingSelection).toBeNull();
    expect(next.chats[CHAT_ID].pendingSelectionMessageId).toBeUndefined();
    expect(next.chats[CHAT_ID].pendingSelectionRunToken).toBeUndefined();
  });

  it("editing a LATER, unrelated message leaves an EARLIER selection card's ownership completely untouched", () => {
    let state = seedChatWithPendingSelection();
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondUserId = "msg-user-2";
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: secondUserId,
        assistantMessageId: secondAssistantId,
        text: "can you also write code?",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const editedNext = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: secondUserId,
        text: "actually, can you write a poem?",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    expect(selectionRecord(editedNext, ASSISTANT_MSG_ID)?.selectionId).toBe(SELECTION_ID);
    expect(editedNext.chats[CHAT_ID].pendingSelectionMessageId).toBe(ASSISTANT_MSG_ID);
    expect(Object.keys(editedNext.chats[CHAT_ID].selectionCards ?? {})).toEqual([ASSISTANT_MSG_ID]);
  });
});

// --- Expandable, sanitized run trace (pre-4H milestone) ---------------------

function traceStep(overrides: Partial<TraceStepDTO> = {}): TraceStepDTO {
  return {
    step_id: "step-1",
    category: "teams",
    label: "Used the selected Teams conversation",
    status: "completed",
    ...overrides,
  };
}

describe("reducer — SEND_MESSAGE seeds an empty run trace record", () => {
  it("seeds chat.runTraces[assistantMessageId] with zero steps, collapsed", () => {
    const state = seedRunningChat();
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID]).toEqual({ steps: [], expanded: false });
  });

  it("does not seed runTraces for the existing mock paths (no runToken)", () => {
    const state = reducer(initialState, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: true,
        userMessageId: USER_MSG_ID,
        assistantMessageId: ASSISTANT_MSG_ID,
        text: "hello",
        attachments: [],
        sources: [],
        timestamp: 1000,
      },
    });
    expect(state.chats[CHAT_ID].runTraces).toBeUndefined();
  });
});

describe("reducer — BACKEND_TRACE_STEP", () => {
  it("appends a step to the run's owning trace record, in order", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ step_id: "step-2", category: "response", label: "Generated the response" }),
      },
    });

    const traceRecord = state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID];
    expect(traceRecord?.steps.map((s) => s.label)).toEqual([
      "Used the selected Teams conversation",
      "Generated the response",
    ]);
  });

  it("translates the wire DTO's snake_case fields into the frontend's camelCase RunTraceStep shape", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ step_id: "step-1", safe_metadata: { message_count: 5 } }),
      },
    });
    const step = state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps[0];
    expect(step).toEqual({
      stepId: "step-1",
      category: "teams",
      label: "Used the selected Teams conversation",
      status: "completed",
      safeMetadata: { message_count: 5 },
    });
  });

  it("drops a trace step for a mismatched/stale runToken (mirrors every other BACKEND_* guard)", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: "some-other-run", messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    expect(next).toBe(state); // unchanged reference -- a true no-op
  });
});

describe("reducer — BACKEND_RUN_COMPLETED freezes the owning trace's duration and outcome", () => {
  it("computes finalDurationSeconds from the run's own runStartedAt, and sets outcome", () => {
    const state = reducer(seedRunningChat(), {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const traceRecord = next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID];
    expect(traceRecord?.outcome).toBe("ok");
    expect(typeof traceRecord?.finalDurationSeconds).toBe("number");
    // The already-recorded step survives the freeze untouched.
    expect(traceRecord?.steps).toHaveLength(1);
  });

  it("sets outcome 'error' for a failed run, distinct from a successful one", () => {
    const next = reducer(seedRunningChat(), {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "error" },
    });
    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].outcome).toBe("error");
  });

  it("never re-increments finalDurationSeconds once frozen (idempotent against a second RUN_COMPLETED-shaped dispatch)", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    const frozen = state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].finalDurationSeconds;

    // A stray duplicate/stale completion for the SAME (now-inactive) run
    // token is already a structural no-op (chat.run is gone -- see the
    // withActiveRun-less guard in BACKEND_RUN_COMPLETED itself), so the
    // frozen value is provably never touched again.
    const next = reducer(state, {
      type: "BACKEND_RUN_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" },
    });
    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].finalDurationSeconds).toBe(frozen);
  });
});

describe("reducer — TOGGLE_RUN_TRACE_EXPANDED", () => {
  it("flips the owning trace record's expanded flag in place, without touching its steps", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].expanded).toBe(false);

    state = reducer(state, { type: "TOGGLE_RUN_TRACE_EXPANDED", payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID } });
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].expanded).toBe(true);
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps).toHaveLength(1);

    state = reducer(state, { type: "TOGGLE_RUN_TRACE_EXPANDED", payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID } });
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].expanded).toBe(false);
  });

  it("is a no-op for a message with no run trace record", () => {
    const state = seedRunningChat();
    const next = reducer(state, {
      type: "TOGGLE_RUN_TRACE_EXPANDED",
      payload: { chatId: CHAT_ID, messageId: "no-such-message" },
    });
    expect(next).toBe(state);
  });

  it("preserves a HISTORICAL trace's own expand/collapse choice across a later user message (never auto-collapsed, unlike actionCards/selectionCards)", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    state = reducer(state, { type: "TOGGLE_RUN_TRACE_EXPANDED", payload: { chatId: CHAT_ID, messageId: ASSISTANT_MSG_ID } });
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].expanded).toBe(true);

    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "follow up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    // The FIRST message's trace is still expanded -- untouched by the
    // new turn -- even though actionCards/selectionCards would have
    // auto-collapsed at this same point.
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].expanded).toBe(true);
    // And the new run got its own fresh, independent, collapsed record.
    expect(state.chats[CHAT_ID].runTraces?.[secondAssistantId]).toEqual({ steps: [], expanded: false });
  });
});

describe("reducer — multiple runs each own their own distinct trace (no cross-assignment)", () => {
  it("run A's steps never leak into run B's trace record", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: RUN_TOKEN,
        messageId: ASSISTANT_MSG_ID,
        step: traceStep({ label: "Reviewed 2 retrieved messages" }),
      },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "thanks",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: {
        chatId: CHAT_ID,
        runToken: "run-2",
        messageId: secondAssistantId,
        step: traceStep({ label: "Generated the response" }),
      },
    });

    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label)).toEqual([
      "Reviewed 2 retrieved messages",
    ]);
    expect(state.chats[CHAT_ID].runTraces?.[secondAssistantId].steps.map((s) => s.label)).toEqual([
      "Generated the response",
    ]);
  });
});

describe("reducer — EDIT_MESSAGE discards a discarded turn's run trace, mirrors actionCards/selectionCards cleanup", () => {
  it("removes the run trace owned by a message that no longer exists after the edit", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID]).toBeDefined();

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: USER_MSG_ID, // the message BEFORE the assistant turn that owns the trace
        text: "a different opening prompt",
        assistantMessageId: "msg-assistant-edited",
        retitle: true,
      },
    });

    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID]).toBeUndefined();
    expect(Object.keys(next.chats[CHAT_ID].runTraces ?? {})).toHaveLength(0);
  });

  it("leaves an EARLIER, surviving message's run trace completely untouched by editing a LATER message", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

    const secondUserId = "msg-user-2";
    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: secondUserId,
        assistantMessageId: secondAssistantId,
        text: "a follow-up",
        attachments: [],
        sources: [],
        timestamp: 2000,
        runToken: "run-2",
      },
    });

    const next = reducer(state, {
      type: "EDIT_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        messageId: secondUserId,
        text: "actually, something else",
        assistantMessageId: "msg-assistant-2-edited",
        retitle: false,
      },
    });

    expect(next.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps).toHaveLength(1);
  });
});

describe("reducer — elapsed timer is continuous across status.clear/message.delta, never reset at first token", () => {
  it("freezes the exact elapsed duration from run start, unaffected by status/trace/delta dispatches in between", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(0);
      // Run starts at t=0.
      let state = reducer(initialState, {
        type: "SEND_MESSAGE",
        payload: {
          chatId: CHAT_ID,
          isNewChat: true,
          userMessageId: USER_MSG_ID,
          assistantMessageId: ASSISTANT_MSG_ID,
          text: "summarize the selected Teams conversation",
          attachments: [],
          sources: [],
          timestamp: 0,
          runToken: RUN_TOKEN,
        },
      });

      // A backend status arrives at t=5s.
      vi.setSystemTime(5000);
      state = reducer(state, {
        type: "BACKEND_STATUS_UPDATE",
        payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, stage: "teams_context", label: "Reviewing the selected Teams conversation", activityKind: null },
      });

      // First token arrives at t=10s -- status.clear, then a delta, then a
      // trace.step. None of these may touch runStartedAt.
      vi.setSystemTime(10000);
      state = reducer(state, { type: "BACKEND_STATUS_CLEAR", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
      state = reducer(state, {
        type: "BACKEND_MESSAGE_DELTA",
        payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "The" },
      });
      state = reducer(state, {
        type: "BACKEND_TRACE_STEP",
        payload: {
          chatId: CHAT_ID,
          runToken: RUN_TOKEN,
          messageId: ASSISTANT_MSG_ID,
          step: traceStep({ category: "teams", label: "Used the selected Teams conversation" }),
        },
      });
      expect(state.chats[CHAT_ID].run?.runStartedAt).toBe(0); // still the original start time

      // Streaming continues to t=15s.
      vi.setSystemTime(15000);
      state = reducer(state, {
        type: "BACKEND_MESSAGE_DELTA",
        payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: " summary." },
      });
      expect(state.chats[CHAT_ID].run?.runStartedAt).toBe(0);

      // The run completes at t=20s.
      vi.setSystemTime(20000);
      state = reducer(state, {
        type: "BACKEND_MESSAGE_COMPLETED",
        payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "The summary." },
      });
      state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });

      expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].finalDurationSeconds).toBe(20);
      expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].outcome).toBe("ok");
      // UI PRESENTATION CORRECTION (post-Phase-2): the run's own live
      // activity trail ("Reviewing the selected Teams conversation") is
      // now folded in ahead of the genuine trace.step milestone at
      // completion — see mergeActivityTrailIntoRunTraceSteps.
      expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].steps.map((s) => s.label)).toEqual([
        "Reviewing the selected Teams conversation",
        "Used the selected Teams conversation",
      ]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("reducer — RUN_STOPPED (pre-4H refinement Stop control)", () => {
  it("clears chat.run and freezes the owning trace with outcome 'stopped'", () => {
    let state = reducer(seedRunningChat(), {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    state = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });

    expect(state.chats[CHAT_ID].run).toBeUndefined();
    const traceRecord = state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID];
    expect(traceRecord?.outcome).toBe("stopped");
    expect(typeof traceRecord?.finalDurationSeconds).toBe("number");
    expect(traceRecord?.steps).toHaveLength(1); // the already-recorded step survives
  });

  it("flips a still-pending message to 'complete' (not 'error') — a stop is not a failure", () => {
    const state = seedRunningChat(); // message is still "pending"
    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });

    expect(next.messages[ASSISTANT_MSG_ID].status).toBe("complete");
    expect(next.messages[ASSISTANT_MSG_ID].errorMessage).toBeUndefined();
  });

  it("preserves partial streamed text rather than discarding it", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "Partial answer" },
    });
    expect(state.messages[ASSISTANT_MSG_ID].status).toBe("streaming");

    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });

    expect(next.messages[ASSISTANT_MSG_ID].text).toBe("Partial answer");
    expect(next.messages[ASSISTANT_MSG_ID].status).toBe("complete");
  });

  it("never touches a message that has already reached a terminal status", () => {
    let state = seedRunningChat();
    state = reducer(state, {
      type: "BACKEND_MESSAGE_COMPLETED",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, content: "Already done." },
    });
    // (Contrived: chat.run still present, as if RUN_STOPPED raced ahead of
    // BACKEND_RUN_COMPLETED — the message itself must stay exactly as is.)
    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    expect(next.messages[ASSISTANT_MSG_ID].text).toBe("Already done.");
    expect(next.messages[ASSISTANT_MSG_ID].status).toBe("complete");
  });

  it("is a no-op for a mismatched/stale runToken", () => {
    const state = seedRunningChat();
    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: "some-other-run" } });
    expect(next).toBe(state);
  });

  it("is a no-op when the chat has no active run at all", () => {
    let state = seedRunningChat();
    state = reducer(state, { type: "BACKEND_RUN_COMPLETED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, outcome: "ok" } });
    const next = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    expect(next).toBe(state);
  });

  it("a late BACKEND_MESSAGE_DELTA for the stopped run's runToken is a no-op after RUN_STOPPED", () => {
    let state = seedRunningChat();
    state = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    const next = reducer(state, {
      type: "BACKEND_MESSAGE_DELTA",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, textDelta: "late text" },
    });
    expect(next).toBe(state); // chat.run is gone, so the runToken guard rejects this
  });

  it("a late BACKEND_TRACE_STEP for the stopped run's runToken is a no-op after RUN_STOPPED", () => {
    let state = seedRunningChat();
    state = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });
    const next = reducer(state, {
      type: "BACKEND_TRACE_STEP",
      payload: { chatId: CHAT_ID, runToken: RUN_TOKEN, messageId: ASSISTANT_MSG_ID, step: traceStep() },
    });
    expect(next).toBe(state);
  });

  it("a subsequent SEND_MESSAGE after a stop starts a genuinely new, independent run", () => {
    let state = seedRunningChat();
    state = reducer(state, { type: "RUN_STOPPED", payload: { chatId: CHAT_ID, runToken: RUN_TOKEN } });

    const secondAssistantId = "msg-assistant-2";
    state = reducer(state, {
      type: "SEND_MESSAGE",
      payload: {
        chatId: CHAT_ID,
        isNewChat: false,
        userMessageId: "msg-user-2",
        assistantMessageId: secondAssistantId,
        text: "try again",
        attachments: [],
        sources: [],
        timestamp: 5000,
        runToken: "run-2",
      },
    });

    expect(state.chats[CHAT_ID].run).toEqual({
      runToken: "run-2",
      assistantMessageId: secondAssistantId,
      currentActivity: null,
      activityTrail: [],
      runStartedAt: 5000,
    });
    // The stopped run's own trace is untouched — a new, independent one
    // was seeded for the new run.
    expect(state.chats[CHAT_ID].runTraces?.[ASSISTANT_MSG_ID].outcome).toBe("stopped");
    expect(state.chats[CHAT_ID].runTraces?.[secondAssistantId]).toEqual({ steps: [], expanded: false });
  });
});
