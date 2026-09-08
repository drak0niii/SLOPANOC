import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ActionCardRecord, ApprovalCardState, Chat } from "../../types";
import type { PendingActionDTO } from "../../api/types";
import approvalCardSource from "./ApprovalCard.tsx?raw";
import approvalCardLibSource from "../../lib/approvalCard.ts?raw";

const mockAppState: {
  state: { chats: Record<string, Chat> };
  approvePendingAction: ReturnType<typeof vi.fn>;
  rejectPendingAction: ReturnType<typeof vi.fn>;
  toggleActionCardCollapsed: ReturnType<typeof vi.fn>;
} = {
  state: { chats: {} },
  approvePendingAction: vi.fn(),
  rejectPendingAction: vi.fn(),
  toggleActionCardCollapsed: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { ApprovalCard } from "./ApprovalCard";

const CHAT_ID = "chat-1";
const MESSAGE_ID = "msg-1";

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

function createChatAction(overrides: Partial<PendingActionDTO> = {}): PendingActionDTO {
  return {
    proposal_id: "p1",
    operation: "teams.createChat",
    status: "pending",
    summary: null,
    title: "Core Network Packet Loss",
    members: ["alice@company.com", "bob@company.com"],
    chat_id: null,
    message: null,
    expires_at: "2026-01-01T00:05:00Z",
    expires_in_seconds: 300,
    expires_in_minutes: 5,
    target_display_name: null,
    ...overrides,
  };
}

function sendMessageAction(overrides: Partial<PendingActionDTO> = {}): PendingActionDTO {
  return {
    proposal_id: "p1",
    operation: "teams.sendMessage",
    status: "pending",
    summary: null,
    title: null,
    members: [],
    chat_id: "internal-chat-id-should-never-render",
    message: "Exact message that will be sent to the team.",
    expires_at: "2026-01-01T00:05:00Z",
    expires_in_seconds: 300,
    expires_in_minutes: 5,
    target_display_name: null,
    ...overrides,
  };
}

/**
 * Seeds `mockAppState.state.chats[CHAT_ID]` with exactly one action-card
 * record (Phase 4G hardening pass — per-message ownership) and renders
 * `<ApprovalCard chatId messageId />` against it.
 *
 * `isCurrent` (default true) controls whether `chat.pendingAction` points
 * at this same proposal — i.e. whether it's still the chat's backend-
 * active, actionable proposal — or has been superseded by some other
 * proposal elsewhere, exactly as happens once a later message creates
 * its own new card.
 */
function renderCard(options: {
  dto: PendingActionDTO;
  approvalCard?: ApprovalCardState | null;
  collapsed?: boolean;
  isCurrent?: boolean;
}) {
  const { dto, approvalCard, collapsed = false, isCurrent = true } = options;
  const record: ActionCardRecord = { proposalId: dto.proposal_id, pendingAction: dto, approvalCard, collapsed };
  mockAppState.state.chats[CHAT_ID] = makeChat({
    pendingAction: isCurrent ? dto : { ...dto, proposal_id: "some-other-proposal" },
    pendingActionMessageId: isCurrent ? MESSAGE_ID : "some-other-message",
    actionCards: { [MESSAGE_ID]: record },
  });
  return render(<ApprovalCard chatId={CHAT_ID} messageId={MESSAGE_ID} />);
}

beforeEach(() => {
  mockAppState.state.chats = {};
  mockAppState.approvePendingAction.mockClear();
  mockAppState.rejectPendingAction.mockClear();
  mockAppState.toggleActionCardCollapsed.mockClear();
});

describe("ApprovalCard — renders nothing without an owning record", () => {
  it("returns null when the chat has no actionCards entry for this messageId", () => {
    mockAppState.state.chats[CHAT_ID] = makeChat();
    render(<ApprovalCard chatId={CHAT_ID} messageId={MESSAGE_ID} />);
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
  });
});

describe("ApprovalCard — pending detail rendering", () => {
  it("renders exact createChat details: title and every participant email, with the action-specific 'Create' button", () => {
    renderCard({ dto: createChatAction() });

    expect(screen.getByText("Core Network Packet Loss")).toBeInTheDocument();
    expect(screen.getByText("alice@company.com, bob@company.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeInTheDocument();
  });

  it("renders exact sendMessage content, with the action-specific 'Send' button", () => {
    renderCard({ dto: sendMessageAction() });

    expect(screen.getByText("Exact message that will be sent to the team.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("POST-B7 Item 1 (corrective pass): renders a backend-formatted plain-text message with structure preserved visually via whitespace-pre-wrap, never as HTML", () => {
    renderCard({
      dto: sendMessageAction({
        message: "Overview:\n\n• Check power\n• Check cabling",
      }),
    });

    // Rendered as one plain-text node — real newlines/bullets, not markup.
    const messageText = screen.getByText(/Overview:/);
    expect(messageText.textContent).toBe("Overview:\n\n• Check power\n• Check cabling");
    expect(messageText.className).toContain("whitespace-pre-wrap");
    expect(document.querySelector("ul")).toBeNull();
    expect(document.querySelector("li")).toBeNull();
    expect(document.querySelector("strong")).toBeNull();
  });

  it("POST-B7 Item 1 (corrective pass): literal angle-bracket text is shown as plain visible text, never parsed as markup", () => {
    renderCard({
      dto: sendMessageAction({ message: '<p>Hi<img src=x onerror="window.__pwned=true"></p>' }),
    });

    // React text interpolation renders this as inert text content — the
    // literal string is visible, never parsed/executed as HTML.
    expect(
      screen.getByText('<p>Hi<img src=x onerror="window.__pwned=true"></p>'),
    ).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("never renders the raw chat_id as the destination for sendMessage — falls back to a neutral placeholder when no display name is available", () => {
    renderCard({ dto: sendMessageAction({ target_display_name: null }) });

    expect(screen.queryByText("internal-chat-id-should-never-render")).not.toBeInTheDocument();
    expect(screen.getByText("Selected Teams conversation")).toBeInTheDocument();
  });
});

describe("ApprovalCard — chat display name (Phase 4G hardening pass)", () => {
  it("shows the authoritative real chat topic when target_display_name is present", () => {
    renderCard({ dto: sendMessageAction({ target_display_name: "SLOPANOC Gateway Group Test" }) });

    expect(screen.getByText("SLOPANOC Gateway Group Test")).toBeInTheDocument();
    expect(screen.queryByText("Selected Teams conversation")).not.toBeInTheDocument();
  });

  it("never renders the raw chat_id even when target_display_name is present", () => {
    renderCard({
      dto: sendMessageAction({
        chat_id: "internal-chat-id-should-never-render",
        target_display_name: "SLOPANOC Gateway Group Test",
      }),
    });

    expect(screen.queryByText("internal-chat-id-should-never-render")).not.toBeInTheDocument();
  });

  it("falls back to the neutral placeholder, never inventing a name, when target_display_name is absent", () => {
    renderCard({ dto: sendMessageAction({ target_display_name: null }) });

    expect(screen.getByText("Selected Teams conversation")).toBeInTheDocument();
  });

  it("the display name is read directly from the structured DTO field — never derived from message/summary text", () => {
    // Structural guarantee mirroring the backend's inspect.getsource()
    // pattern: proves the component has no parsing/derivation logic that
    // could infer a name from `message`/`summary` prose -- it only ever
    // reads `pendingAction.target_display_name` verbatim.
    expect(approvalCardSource).not.toContain(".match(");
    expect(approvalCardSource).not.toContain(".split(");
    expect(approvalCardSource).toContain("pendingAction.target_display_name");
  });

  it("createChat continues to use `title` for its destination-equivalent identity, not target_display_name", () => {
    renderCard({ dto: createChatAction({ title: "Ops Bridge" }) });

    expect(screen.getByText("Ops Bridge")).toBeInTheDocument();
    expect(screen.queryByText("Selected Teams conversation")).not.toBeInTheDocument();
  });
});

describe("ApprovalCard — action-specific button labels (Phase 4G hardening pass)", () => {
  it("never renders any generic 'Approve' phrasing for createChat", () => {
    renderCard({ dto: createChatAction() });

    expect(screen.queryByRole("button", { name: /approve & create/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve & create$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/approve & create/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm action/i })).not.toBeInTheDocument();
  });

  it("never renders any generic 'Approve' phrasing for sendMessage", () => {
    renderCard({ dto: sendMessageAction() });

    expect(screen.queryByText(/approve & send/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm action/i })).not.toBeInTheDocument();
  });

  it("Reject remains 'Reject' regardless of operation", () => {
    renderCard({ dto: createChatAction() });
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });
});

describe("ApprovalCard — expiry metadata is never rendered", () => {
  it("never renders expires_at/expires_in_seconds/expires_in_minutes in any state", () => {
    renderCard({
      dto: sendMessageAction({
        expires_at: "2026-06-15T09:30:00Z",
        expires_in_seconds: 271828,
        expires_in_minutes: 4530,
      }),
    });

    const text = document.body.textContent ?? "";
    expect(text).not.toContain("2026-06-15T09:30:00Z");
    expect(text).not.toContain("271828");
    expect(text).not.toContain("4530");
    expect(text.toLowerCase()).not.toContain("expires in");
  });

  it("never renders payload_hash or the raw status string anywhere", () => {
    renderCard({ dto: sendMessageAction() });

    const text = document.body.textContent ?? "";
    expect(text).not.toContain("payload_hash");
  });

  it("contains no client-side timer/countdown logic (Phase 4G hardening pass)", () => {
    // Structural guarantee mirroring the backend's inspect.getsource()
    // pattern (see backend/tests/test_teams_dynamic_expiry_presentation.py):
    // proves the "no visible timer" requirement isn't satisfied by simply
    // not rendering the fields today, but by never having computed a
    // countdown from them in the first place -- so a future edit can't
    // reintroduce one by wiring up expires_in_seconds/expires_at locally.
    // Also proves collapse is never timer-driven (Phase 4G hardening pass).
    const sources = [approvalCardSource, approvalCardLibSource];

    for (const source of sources) {
      expect(source).not.toContain("setInterval");
      expect(source).not.toContain("setTimeout");
      expect(source).not.toContain("Date.now(");
      expect(source.toLowerCase()).not.toContain("countdown");
    }
  });
});

describe("ApprovalCard — collapse / expand (Phase 4G hardening pass)", () => {
  it("collapsed renders only the status icon, title, and chevron — no detail rows, no error text, no buttons", () => {
    renderCard({ dto: createChatAction(), collapsed: true });

    expect(screen.getByRole("group")).toBeInTheDocument();
    expect(screen.getByText("Action requires approval")).toBeInTheDocument();
    expect(screen.queryByText("Core Network Packet Loss")).not.toBeInTheDocument();
    expect(screen.queryByText("alice@company.com, bob@company.com")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("collapsed header has aria-expanded=false; expanded header has aria-expanded=true", () => {
    const { unmount } = renderCard({ dto: sendMessageAction(), collapsed: true });
    expect(screen.getByRole("button", { name: "Action requires approval" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    unmount();

    renderCard({ dto: sendMessageAction(), collapsed: false });
    expect(screen.getByRole("button", { name: "Action requires approval" })).toHaveAttribute("aria-expanded", "true");
  });

  it("expanded shows full detail content and, for the current proposal, its action buttons", () => {
    renderCard({ dto: sendMessageAction(), collapsed: false });

    expect(screen.getByText("Exact message that will be sent to the team.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });

  it("clicking the collapsed header calls toggleActionCardCollapsed with exactly chatId and messageId — never mutates locally", () => {
    renderCard({ dto: sendMessageAction(), collapsed: true });

    fireEvent.click(screen.getByRole("button", { name: "Action requires approval" }));

    expect(mockAppState.toggleActionCardCollapsed).toHaveBeenCalledExactlyOnceWith(CHAT_ID, MESSAGE_ID);
    // The click alone does not locally reveal detail content — state is
    // owned by the reducer; a real click would trigger a rerender via a
    // new `collapsed` prop, not a local toggle.
    expect(screen.queryByText("Exact message that will be sent to the team.")).not.toBeInTheDocument();
  });

  it("clicking the expanded header's chevron also calls toggleActionCardCollapsed (collapsing back)", () => {
    renderCard({ dto: sendMessageAction(), collapsed: false });

    fireEvent.click(screen.getByRole("button", { name: "Action requires approval" }));

    expect(mockAppState.toggleActionCardCollapsed).toHaveBeenCalledExactlyOnceWith(CHAT_ID, MESSAGE_ID);
  });

  it("a still-pending historical card remains fully actionable once expanded, as long as it's still the chat's current proposal", () => {
    renderCard({ dto: sendMessageAction(), collapsed: false, isCurrent: true });

    const sendButton = screen.getByRole("button", { name: "Send" });
    const rejectButton = screen.getByRole("button", { name: "Reject" });
    expect(sendButton).not.toBeDisabled();
    expect(rejectButton).not.toBeDisabled();

    fireEvent.click(sendButton);
    expect(mockAppState.approvePendingAction).toHaveBeenCalledExactlyOnceWith(CHAT_ID, "p1");
  });

  it("a pending card that is NO LONGER the chat's current proposal (superseded elsewhere) renders no action buttons at all", () => {
    renderCard({ dto: sendMessageAction(), collapsed: false, isCurrent: false });

    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("terminal states (e.g. completed) stay collapsible and preserve their exact historical content once re-expanded", () => {
    const { unmount } = renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "completed", executedAction: null },
      collapsed: true,
    });
    expect(screen.getByText("Action completed")).toBeInTheDocument();
    expect(screen.queryByText(/message sent\./i)).not.toBeInTheDocument();
    unmount();

    renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "completed", executedAction: null },
      collapsed: false,
    });
    expect(screen.getByText("Action completed")).toBeInTheDocument();
    expect(screen.getByText(/message sent\./i)).toBeInTheDocument();
  });
});

describe("ApprovalCard — typography scoping (Phase 4G hardening pass)", () => {
  it("the title uses the normal UI font (no mono class), the body/detail rows use the Courier Sans scoped class", () => {
    renderCard({ dto: createChatAction() });

    const title = screen.getByText("Action requires approval");
    expect(title.className).not.toContain("approval-card-mono");

    const detailValue = screen.getByText("Core Network Packet Loss");
    expect(detailValue.closest(".approval-card-mono")).not.toBeNull();
  });

  it("buttons never receive the mono class", () => {
    renderCard({ dto: sendMessageAction() });

    expect(screen.getByRole("button", { name: "Send" }).className).not.toContain("approval-card-mono");
    expect(screen.getByRole("button", { name: "Reject" }).className).not.toContain("approval-card-mono");
  });

  it("error/status body text (e.g. an expired denial) uses the mono class", () => {
    renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "expired", message: "This proposal has expired." },
    });

    expect(screen.getByText("This proposal has expired.").className).toContain("approval-card-mono");
  });

  it("the typography rule is scoped to ApprovalCard via one canonical class, never an inline font-family override", () => {
    // Vitest stubs out .css imports (even with ?raw) by default, so this
    // is checked from the component source instead of index.css directly:
    // the ONLY place "Courier"/"mono" typography is ever mentioned is the
    // single shared className token "approval-card-mono" — the component
    // never declares an inline `style={{ fontFamily: ... }}` override
    // (which would risk leaking outside a scoped class) and never
    // references "Courier" directly (the font choice itself lives only in
    // index.css's `.approval-card-mono` rule, not duplicated here).
    expect(approvalCardSource).not.toContain("Courier");
    expect(approvalCardSource).not.toContain("fontFamily");
    expect(approvalCardSource).not.toContain("style={{");
    const monoClassOccurrences = approvalCardSource.split("approval-card-mono").length - 1;
    expect(monoClassOccurrences).toBeGreaterThan(0);
  });
});

describe("ApprovalCard — Approve flow", () => {
  it("calls approvePendingAction with exactly the chatId and this card's proposalId when its button is clicked", () => {
    renderCard({ dto: sendMessageAction() });

    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(mockAppState.approvePendingAction).toHaveBeenCalledExactlyOnceWith(CHAT_ID, "p1");
  });

  it("calls rejectPendingAction with exactly the chatId and this card's proposalId when Reject is clicked", () => {
    renderCard({ dto: sendMessageAction() });

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(mockAppState.rejectPendingAction).toHaveBeenCalledExactlyOnceWith(CHAT_ID, "p1");
  });

  it("disables both buttons while approving/executing — double-click protected", () => {
    renderCard({ dto: sendMessageAction(), approvalCard: { proposalId: "p1", phase: "approving" } });

    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it('approval alone (phase "executing") never renders completed copy', () => {
    renderCard({ dto: sendMessageAction(), approvalCard: { proposalId: "p1", phase: "executing" } });

    expect(screen.queryByText(/message sent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/chat created/i)).not.toBeInTheDocument();
    expect(screen.getByText(/sending the message/i)).toBeInTheDocument();
  });

  it("execute success renders the correct completed copy for sendMessage", () => {
    renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "completed", executedAction: { chatId: "c1", title: null, webUrl: null } },
    });

    expect(screen.getByText("Action completed")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/message sent/i);
  });

  it("execute success renders the correct completed copy for createChat, with a link when webUrl is present", () => {
    renderCard({
      dto: createChatAction(),
      approvalCard: {
        proposalId: "p1",
        phase: "completed",
        executedAction: { chatId: "c1", title: "Ops Bridge", webUrl: "https://teams.microsoft.com/x" },
      },
    });

    expect(screen.getByText("Action completed")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/chat created/i);
    expect(screen.getByRole("link", { name: "Open in Teams" })).toHaveAttribute(
      "href",
      "https://teams.microsoft.com/x",
    );
  });
});

describe("ApprovalCard — terminal states", () => {
  it("rejection renders the same card as a muted terminal state, non-actionable", () => {
    renderCard({ dto: sendMessageAction({ status: "rejected" }) });

    expect(screen.getByText("Action rejected")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/action rejected/i);
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it('a structured "proposal_expired" denial renders Expired, non-actionable, no timer text', () => {
    renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "expired", message: "This proposal has expired." },
    });

    expect(screen.getByText("Approval expired")).toBeInTheDocument();
    expect(screen.getByText("This proposal has expired.")).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(1); // only the collapse header toggle remains
    expect((document.body.textContent ?? "").toLowerCase()).not.toMatch(/\d+\s*(minute|second)s?\s*(left|remaining)/);
  });

  it("a stale-proposal denial renders Failed, non-actionable, with the exact backend message", () => {
    renderCard({
      dto: sendMessageAction(),
      approvalCard: {
        proposalId: "p1",
        phase: "failed",
        message: "This proposal is no longer the active one for this session -- it may have been replaced.",
      },
    });

    expect(screen.getByText("Action failed")).toBeInTheDocument();
    expect(
      screen.getByText("This proposal is no longer the active one for this session -- it may have been replaced."),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(1); // only the collapse header toggle remains
  });

  it("an unconfirmed (ambiguous) outcome renders its own distinct, non-actionable state", () => {
    renderCard({ dto: sendMessageAction(), approvalCard: { proposalId: "p1", phase: "unconfirmed" } });

    expect(screen.getByText(/could not be confirmed/i)).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(1); // only the collapse header toggle remains
    // Distinct from "failed" — no auto-retry control, no danger-styled failure copy.
    expect(screen.queryByText("Action failed")).not.toBeInTheDocument();
  });

  it('execute failure (a definite failure) renders "Action failed", not "Action completed"', () => {
    renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "failed", message: "The Teams connector refused the request." },
    });

    expect(screen.getByText("Action failed")).toBeInTheDocument();
    expect(screen.getByText("The Teams connector refused the request.")).toBeInTheDocument();
    expect(screen.queryByText("Action completed")).not.toBeInTheDocument();
  });
});

describe("ApprovalCard — accessibility", () => {
  it("exposes a labeled group and keyboard-reachable, distinctly-labeled buttons", () => {
    renderCard({ dto: sendMessageAction() });

    expect(screen.getByRole("group", { name: /pending teams action approval/i })).toBeInTheDocument();
    const approveButton = screen.getByRole("button", { name: "Send" });
    const rejectButton = screen.getByRole("button", { name: "Reject" });
    expect(approveButton.tagName).toBe("BUTTON");
    expect(rejectButton.tagName).toBe("BUTTON");
  });

  it("sets aria-busy while a request is in flight", () => {
    renderCard({ dto: sendMessageAction(), approvalCard: { proposalId: "p1", phase: "approving" } });

    expect(screen.getByRole("group")).toHaveAttribute("aria-busy", "true");
  });

  it("does not encode state by color alone — each state has distinguishing text", () => {
    const { rerender } = renderCard({
      dto: sendMessageAction(),
      approvalCard: { proposalId: "p1", phase: "failed", message: "x" },
    });
    expect(screen.getByText("Action failed")).toBeInTheDocument();

    mockAppState.state.chats[CHAT_ID] = makeChat({
      pendingAction: sendMessageAction(),
      pendingActionMessageId: MESSAGE_ID,
      actionCards: {
        [MESSAGE_ID]: {
          proposalId: "p1",
          pendingAction: sendMessageAction(),
          approvalCard: { proposalId: "p1", phase: "unconfirmed" },
          collapsed: false,
        },
      },
    });
    rerender(<ApprovalCard chatId={CHAT_ID} messageId={MESSAGE_ID} />);
    expect(screen.getByText(/execution status could not be confirmed/i)).toBeInTheDocument();
  });
});
