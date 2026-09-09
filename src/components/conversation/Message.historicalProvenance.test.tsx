import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

// B7 corrective pass — durable, turn-owned Source/Knowledge-source
// provenance rehydration. Mirrors Message.persistedAttachments.test.tsx's
// own real-AppStateProvider integration pattern exactly: this exercises
// the REAL boot -> select -> lazy history -> render pipeline (never a
// mocked useAppState), so a passing test here proves the actual wiring
// (session_history_service.py's DTO -> AppState.tsx's HISTORY_FETCH_
// SUCCEEDED reducer -> Message.tsx -> SourceChip.tsx) end to end on the
// frontend side of this feature. SourceChip.test.tsx already covers the
// formatter/label edge cases exhaustively at the component-unit level —
// this file deliberately does not re-litigate those, only the hydration
// wiring itself (Step 8's 10 numbered requirements).
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
import type { KnowledgeSourceReferenceDTO, SourceReferenceDTO } from "../../api/types";
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

/** Minimal stand-in for MessageList, same as Message.persistedAttachments.test.tsx. */
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

const TEAMS_SOURCE: SourceReferenceDTO = {
  source_id: "src-ops-bridge",
  source_type: "teams",
  label: "Ops Bridge",
  title: "Ops Bridge",
  message_count: 4,
  period_start: "2026-08-20T09:00:00Z",
  period_end: "2026-08-20T09:05:00Z",
  contributors: ["Priya"],
  evidence: [{ author: "Priya", sent_at: "2026-08-20T09:00:00Z", snippet: "TEAM-ORION is the platform owner." }],
};

function kmSource(overrides: Partial<KnowledgeSourceReferenceDTO> = {}): KnowledgeSourceReferenceDTO {
  return {
    source_id: "ks-verification",
    source_type: "knowledge",
    label: "Governed knowledge",
    knowledge_id: "aurora-relay",
    version_label: "v1",
    section_id: "aurora-relay:v1:s0",
    title: "Aurora Relay Verification Procedure",
    document_type: "technical_instruction",
    source_system: "test",
    evidence_source_id: "aurora-relay-doc",
    source_display_name: "Aurora Relay Governed Test Procedure",
    section_heading: "Verification",
    source_locator: "p1",
    content: "Confirm the relay checksum is exactly 7319 and the status indicator is GREEN.",
    ...overrides,
  };
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

async function seedAndOpen(messages: unknown[]) {
  listSavedSessions.mockResolvedValue({
    sessions: [{ session_id: "s1", title: "Fixture Chat", updated_at: "2026-08-20T09:05:00.000000+00:00" }],
  });
  getSessionHistory.mockResolvedValue({ session_id: "s1", messages });

  renderHarness();
  await waitFor(() => expect(latest.state.chats.s1).toBeDefined());
  await act(async () => {
    latest.selectChat("s1");
  });
  await waitFor(() => expect(latest.state.chats.s1.historyHydrationStatus).toBe("loaded"));
}

describe("Message + AppState — B7 corrective pass: historical Source/Knowledge-source rehydration", () => {
  it("1. a history-hydrated assistant message receives its governed-KM SourceReferences and renders a Source chip", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:user",
        turn_id: "e-1",
        role: "user",
        text: "Check governed knowledge for the Aurora relay.",
        created_at: "2026-08-20T09:00:00.000000+00:00",
        attachments: [],
        knowledge_sources: [],
      },
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified per the governed procedure.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource()],
      },
    ]);

    expect(latest.state.chats.s1.knowledgeSources?.["e-1:assistant"]).toEqual([kmSource()]);
    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v1/ }),
    ).toBeInTheDocument();
  });

  it("2. two governed-KM references from the same document+version consolidate into ONE chip covering both matched sections after hydration (POST-A5 refinement, Track B)", async () => {
    const verification = kmSource({ source_id: "ks-verification", section_id: "aurora-relay:v1:s0" });
    const escalation = kmSource({
      source_id: "ks-escalation",
      section_id: "aurora-relay:v1:s1",
      section_heading: "Escalation",
      content: "If verification fails, collect the observed values and escalate to the platform owner.",
    });
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verify, and escalate if it fails.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [verification, escalation],
      },
    ]);

    // The underlying trusted per-section provenance is still fully
    // preserved (two distinct SourceReferenceDTO entries) -- only the
    // message-level PRESENTATION consolidates into one chip.
    expect(latest.state.chats.s1.knowledgeSources?.["e-1:assistant"]).toHaveLength(2);
    const sourceButtons = screen.getAllByRole("button", { name: /Source ·/ });
    expect(sourceButtons).toHaveLength(1);
    expect(sourceButtons[0]).toHaveTextContent("Source · Aurora Relay Verification Procedure · v1");

    fireEvent.click(sourceButtons[0]);
    expect(screen.getAllByText("Verification").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Escalation").length).toBeGreaterThan(0);
  });

  it("3. the hydrated chip's label uses the exact '<title> · <version_label>' consolidated group format", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource()],
      },
    ]);
    const button = screen.getByRole("button", { name: /Source ·/ });
    expect(button.textContent?.replace(/\s+/g, " ").trim()).toBe("Source · Aurora Relay Verification Procedure · v1");
  });

  it("4. Teams + governed-KM sources on the same historical answer render together", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Combined Teams and governed-knowledge answer.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        source: TEAMS_SOURCE,
        knowledge_sources: [kmSource()],
      },
    ]);

    expect(screen.getByRole("button", { name: /Source · Ops Bridge/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v1/ }),
    ).toBeInTheDocument();
  });

  it("5. hydration renders through the exact same SourceChip/groupKnowledgeSourceReferences path the live SSE flow uses — no separate 'historical' rendering", async () => {
    // Message.tsx's rendering branch (chat.knowledgeSources ->
    // groupKnowledgeSourceReferences -> SourceChip kind="knowledge-group")
    // is a single, unconditional code path reached identically whether
    // `chat.knowledgeSources` was populated by HISTORY_FETCH_SUCCEEDED
    // (this test) or by BACKEND_MESSAGE_COMPLETED (SourceChip.groups.
    // test.tsx's own direct-render unit tests) — there is no separate
    // "historical" branch to diverge. Asserting the exact string here, for
    // a DTO SourceChip.groups.test.tsx also exercises directly, is
    // sufficient proof of convergence.
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource()],
      },
    ]);
    const label = screen
      .getByRole("button", { name: /Source ·/ })
      .textContent?.replace(/\s+/g, " ")
      .trim();
    expect(label).toBe("Source · Aurora Relay Verification Procedure · v1");
  });

  it("6. no source_uri or gs:// URI ever appears in the hydrated provenance rendering", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        source: TEAMS_SOURCE,
        knowledge_sources: [kmSource()],
      },
    ]);
    // Opened one at a time — Radix's Dialog marks background content inert
    // while a drawer is open, so both triggers are only queryable when no
    // drawer is currently open.
    fireEvent.click(screen.getByRole("button", { name: /Source · Ops Bridge/ }));
    const teamsDrawerText = document.body.textContent ?? "";
    expect(teamsDrawerText).not.toContain("gs://");
    expect(teamsDrawerText.toLowerCase()).not.toContain("source_uri");
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: /Source · Aurora Relay/ }));
    const knowledgeDrawerText = document.body.textContent ?? "";
    expect(knowledgeDrawerText).not.toContain("gs://");
    expect(knowledgeDrawerText.toLowerCase()).not.toContain("source_uri");
  });

  it("7. missing optional KM metadata (no section_heading, no source_display_name) falls back gracefully after hydration", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource({ section_heading: null, source_display_name: null })],
      },
    ]);
    expect(
      screen.getByRole("button", { name: /^Source · Aurora Relay Verification Procedure · v1$/ }),
    ).toBeInTheDocument();
  });

  it("8. a historical text-only message with no source/knowledge_sources renders no Source chip", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:user",
        turn_id: "e-1",
        role: "user",
        text: "hello",
        created_at: "2026-08-20T09:00:00.000000+00:00",
        attachments: [],
        knowledge_sources: [],
      },
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "hi",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [],
      },
    ]);
    expect(latest.state.chats.s1.sources).toBeUndefined();
    expect(latest.state.chats.s1.knowledgeSources).toBeUndefined();
    expect(screen.queryByRole("button", { name: /Source ·/ })).not.toBeInTheDocument();
  });

  it("9. a historical message with BOTH a persisted image attachment and governed-KM provenance renders both (B4D unaffected)", async () => {
    getAttachmentContent.mockResolvedValue(pngBlob());
    await seedAndOpen([
      {
        message_id: "e-1:user",
        turn_id: "e-1",
        role: "user",
        text: "here's a screenshot, check governed knowledge",
        created_at: "2026-08-20T09:00:00.000000+00:00",
        attachments: [{ attachment_id: "att-1", filename: "screenshot.png", mime_type: "image/png", size_bytes: 12345 }],
        knowledge_sources: [],
      },
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified per the governed procedure.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource()],
      },
    ]);

    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("alt", "Attached image: screenshot.png");
    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v1/ }),
    ).toBeInTheDocument();
  });

  it("10. hydration with no selection/action data leaves selectionCards/actionCards absent (SelectionCard/ApprovalCard hydration unchanged)", async () => {
    await seedAndOpen([
      {
        message_id: "e-1:assistant",
        turn_id: "e-1",
        role: "assistant",
        text: "Verified.",
        created_at: "2026-08-20T09:00:05.000000+00:00",
        attachments: [],
        knowledge_sources: [kmSource()],
      },
    ]);
    expect(latest.state.chats.s1.actionCards).toBeUndefined();
    expect(latest.state.chats.s1.selectionCards).toBeUndefined();
    expect(latest.state.chats.s1.runTraces).toBeUndefined();
    // The provenance addition itself is present, proving these are
    // independently-absent (not just "the whole hydration path is broken").
    expect(latest.state.chats.s1.knowledgeSources?.["e-1:assistant"]).toHaveLength(1);
  });
});
