import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chat, SelectionCardRecord, SelectionCardState } from "../../types";
import type { PendingSelectionDTO } from "../../api/types";
import selectionCardSource from "./SelectionCard.tsx?raw";

const mockAppState: {
  state: { chats: Record<string, Chat> };
  chooseSelectionOption: ReturnType<typeof vi.fn>;
  skipSelectionOption: ReturnType<typeof vi.fn>;
  toggleSelectionCardCollapsed: ReturnType<typeof vi.fn>;
} = {
  state: { chats: {} },
  chooseSelectionOption: vi.fn(),
  skipSelectionOption: vi.fn(),
  toggleSelectionCardCollapsed: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { SelectionCard } from "./SelectionCard";

const CHAT_ID = "chat-1";
const MESSAGE_ID = "msg-1";
const SELECTION_ID = "sel1";

function makeChat(overrides: Partial<Chat> = {}): Chat {
  return {
    id: CHAT_ID,
    title: "Chat",
    projectId: null,
    pinned: false,
    createdAt: 0,
    messageIds: [],
    activeSkillId: null,
    connectorIds: [],
    selectedModelId: "default",
    thinkingEffort: "instant",
    ...overrides,
  };
}

function dto(overrides: Partial<PendingSelectionDTO> = {}): PendingSelectionDTO {
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

/**
 * Seeds `mockAppState.state.chats[CHAT_ID]` with exactly one selection
 * card record and renders `<SelectionCard chatId messageId />` against
 * it. `isCurrent` (default true) controls whether `chat.pendingSelection`
 * still points at this same selection — false simulates a stale/
 * superseded card (the conversation moved past it without resolving it).
 */
function renderCard(options: {
  selection?: PendingSelectionDTO;
  selectionCard?: SelectionCardState | null;
  collapsed?: boolean;
  isCurrent?: boolean;
}) {
  const { selection = dto(), selectionCard, collapsed = false, isCurrent = true } = options;
  const record: SelectionCardRecord = {
    selectionId: selection.selection_id,
    pendingSelection: selection,
    selectionCard,
    collapsed,
  };
  mockAppState.state.chats[CHAT_ID] = makeChat({
    pendingSelection: isCurrent ? selection : { ...selection, selection_id: "some-other-selection" },
    pendingSelectionMessageId: isCurrent ? MESSAGE_ID : "some-other-message",
    selectionCards: { [MESSAGE_ID]: record },
  });
  return render(<SelectionCard chatId={CHAT_ID} messageId={MESSAGE_ID} />);
}

beforeEach(() => {
  mockAppState.state.chats = {};
  mockAppState.chooseSelectionOption.mockClear();
  mockAppState.skipSelectionOption.mockClear();
  mockAppState.toggleSelectionCardCollapsed.mockClear();
});

describe("SelectionCard — renders nothing without an owning record", () => {
  it("returns null when the chat has no selectionCards entry for this messageId", () => {
    mockAppState.state.chats[CHAT_ID] = makeChat();
    render(<SelectionCard chatId={CHAT_ID} messageId={MESSAGE_ID} />);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});

describe("SelectionCard — pending option rendering", () => {
  it("renders every option's label, and never a raw chat id (not present anywhere in the DTO)", () => {
    renderCard({});
    expect(screen.getByRole("button", { name: /Project Falcon Room Test/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Project Falcon Test/ })).toBeInTheDocument();
  });

  it("renders at most the options the DTO actually provided — never more, never fabricated", () => {
    renderCard({ selection: dto({ options: [{ option_id: "opt1", label: "Only Candidate" }] }) });
    expect(screen.getAllByRole("button").filter((b) => b.textContent?.includes("Only Candidate"))).toHaveLength(1);
    expect(screen.queryByText("Project Falcon Test")).not.toBeInTheDocument();
  });

  it("renders a Skip action alongside the options", () => {
    renderCard({});
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });
});

describe("SelectionCard — choosing an option", () => {
  it("clicking an option calls chooseSelectionOption with the exact chatId/selectionId/option_id", () => {
    renderCard({});
    fireEvent.click(screen.getByRole("button", { name: /Project Falcon Room Test/ }));
    expect(mockAppState.chooseSelectionOption).toHaveBeenCalledWith(CHAT_ID, SELECTION_ID, "opt1");
  });

  it("clicking Skip calls skipSelectionOption with the exact chatId/selectionId", () => {
    renderCard({});
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(mockAppState.skipSelectionOption).toHaveBeenCalledWith(CHAT_ID, SELECTION_ID);
  });

  it("options and Skip are not offered once the card is no longer current (stale/superseded)", () => {
    renderCard({ isCurrent: false });
    expect(screen.queryByRole("button", { name: /Project Falcon Room Test/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("option buttons and Skip are disabled while choosing/skipping is in flight", () => {
    renderCard({ selectionCard: { selectionId: SELECTION_ID, phase: "choosing" } });
    // Busy state shows a spinner in place of the Skip button, and every
    // option is disabled — no double-submit possible.
    expect(screen.getByRole("button", { name: /Project Falcon Room Test/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });
});

describe("SelectionCard — terminal states", () => {
  it("resolved shows the real selected label, never inventing one", () => {
    renderCard({
      selectionCard: { selectionId: SELECTION_ID, phase: "resolved", selectedLabel: "Project Falcon Room Test" },
    });
    expect(screen.getByText(/Teams chat selected: Project Falcon Room Test/)).toBeInTheDocument();
    // No longer actionable.
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("skipped shows a fixed, non-actionable message", () => {
    renderCard({ selectionCard: { selectionId: SELECTION_ID, phase: "skipped" } });
    expect(screen.getByText("No Teams chat selected.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Project Falcon Room Test/ })).not.toBeInTheDocument();
  });

  it("failed shows the real backend message", () => {
    renderCard({
      selectionCard: { selectionId: SELECTION_ID, phase: "failed", message: "That option does not belong to this selection." },
    });
    expect(screen.getByText("That option does not belong to this selection.")).toBeInTheDocument();
  });

  it("stale (superseded elsewhere) is non-actionable and explains itself", () => {
    renderCard({ isCurrent: false });
    expect(screen.getByText(/no longer active/)).toBeInTheDocument();
  });
});

describe("SelectionCard — collapse/expand", () => {
  it("clicking the header toggles collapse via toggleSelectionCardCollapsed", () => {
    renderCard({});
    fireEvent.click(screen.getByRole("button", { name: "Which chat did you mean?" }));
    expect(mockAppState.toggleSelectionCardCollapsed).toHaveBeenCalledWith(CHAT_ID, MESSAGE_ID);
  });

  it("shows a structured collapsed label (not a hardcoded literal example) when collapsed", () => {
    renderCard({
      collapsed: true,
      selectionCard: { selectionId: SELECTION_ID, phase: "resolved", selectedLabel: "Project Falcon Room Test" },
    });
    expect(screen.getByText("Teams chat selected: Project Falcon Room Test")).toBeInTheDocument();
    // The full option list is not rendered while collapsed.
    expect(screen.queryByRole("button", { name: /Project Falcon Test/ })).not.toBeInTheDocument();
  });
});

describe("SelectionCard — exactly one status icon, never a duplicated glyph in the title text", () => {
  it("resolved: renders the success icon once, and the title text carries no literal checkmark", () => {
    renderCard({
      collapsed: true,
      selectionCard: { selectionId: SELECTION_ID, phase: "resolved", selectedLabel: "Project Falcon Room Test" },
    });
    // Exactly one status icon (the header's StatusIcon svg).
    const group = screen.getByRole("group");
    expect(group.querySelectorAll("svg.text-success")).toHaveLength(1);
    // The title text itself never embeds a matching glyph alongside it.
    const title = screen.getByText("Teams chat selected: Project Falcon Room Test");
    expect(title.textContent).not.toMatch(/[✓✔]/);
  });

  it("skipped: renders the icon once, title carries no literal '×' glyph", () => {
    renderCard({ collapsed: true, selectionCard: { selectionId: SELECTION_ID, phase: "skipped" } });
    const group = screen.getByRole("group");
    expect(group.querySelectorAll("svg")).not.toHaveLength(0);
    const title = screen.getByText("No Teams chat selected");
    expect(title.textContent).not.toMatch(/[×✗]/);
  });

  it("stale (superseded): title carries no duplicated glyph either", () => {
    renderCard({ collapsed: true, isCurrent: false });
    const title = screen.getByText("Teams chat selection no longer active");
    expect(title.textContent).not.toMatch(/[✓✔×✗]/);
  });
});

describe("SelectionCard — never a hardcoded example, never a raw chat id", () => {
  it("never references the literal example chat names from the spec, nor a raw chat_id property access", () => {
    expect(selectionCardSource).not.toContain("SLOPANOC Gateway Group");
    // Structural guarantee: the DTO has no chat_id field at all, so the
    // only way this component could leak one is a property access like
    // `.chat_id` — the word appears only in an explanatory comment above.
    expect(selectionCardSource).not.toContain(".chat_id");
    expect(selectionCardSource).not.toContain("I've prepared");
  });

  it("labels come only from the structured DTO's option.label — never parsed/derived text", () => {
    expect(selectionCardSource).not.toContain(".match(");
    expect(selectionCardSource).not.toContain(".split(");
    expect(selectionCardSource).toContain("option.label");
  });
});
