import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chat, Message as MessageType } from "../../types";
import composerSource from "./PromptComposer.tsx?raw";

// PromptComposer's own send-gating logic is what this file tests — its
// sibling composer controls (skills/thinking-effort/attachments/etc.) are
// stubbed out entirely so this file doesn't need to replicate their own,
// unrelated useAppState() needs.
vi.mock("./ComposerPlusMenu", () => ({ ComposerPlusMenu: () => null }));
vi.mock("./ThinkingEffortSelector", () => ({ ThinkingEffortSelector: () => null }));
vi.mock("./SkillSelector", () => ({ SkillSelector: () => null }));
vi.mock("./MicrophoneButton", () => ({ MicrophoneButton: () => null }));
vi.mock("./AttachmentChipRow", () => ({ AttachmentChipRow: () => null }));
vi.mock("./SourceChipRow", () => ({ SourceChipRow: () => null }));

const mockAppState: {
  state: { draft: { text: string; attachments: unknown[]; sources: unknown[] }; composerNudgeAt: number };
  activeChat: Chat | null;
  activeMessages: MessageType[];
  setDraftText: ReturnType<typeof vi.fn>;
  addAttachments: ReturnType<typeof vi.fn>;
  sendMessage: ReturnType<typeof vi.fn>;
  stopActiveRun: ReturnType<typeof vi.fn>;
} = {
  state: { draft: { text: "", attachments: [], sources: [] }, composerNudgeAt: 0 },
  activeChat: null,
  activeMessages: [],
  setDraftText: vi.fn(),
  addAttachments: vi.fn(),
  sendMessage: vi.fn(),
  stopActiveRun: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { PromptComposer } from "./PromptComposer";

function makeChat(overrides: Partial<Chat> = {}): Chat {
  return {
    id: "chat-1",
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

function makeMessage(overrides: Partial<MessageType> = {}): MessageType {
  return {
    id: "msg-1",
    chatId: "chat-1",
    role: "assistant",
    text: "",
    status: "pending",
    createdAt: 0,
    ...overrides,
  };
}

beforeEach(() => {
  mockAppState.state = { draft: { text: "", attachments: [], sources: [] }, composerNudgeAt: 0 };
  mockAppState.activeChat = null;
  mockAppState.activeMessages = [];
  mockAppState.sendMessage.mockClear();
  mockAppState.stopActiveRun.mockClear();
});

describe("PromptComposer — duplicate-submit / active-run guard", () => {
  it("allows sending when idle with typed text", () => {
    mockAppState.state.draft.text = "hello";
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(mockAppState.sendMessage).toHaveBeenCalledOnce();
  });

  it("replaces Send with a Stop control while a real backend run is active (chat.run present)", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /waiting for the assistant/i })).not.toBeInTheDocument();
    const stopButton = screen.getByRole("button", { name: "Stop generating" });
    expect(stopButton).not.toBeDisabled();
  });

  it("blocks Enter from submitting while a run is active", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    const textarea = screen.getByLabelText("Message");
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(mockAppState.sendMessage).not.toHaveBeenCalled();
  });

  it("blocks sending while the last message is still pending or streaming (mock-path parity)", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.activeMessages = [makeMessage({ status: "pending" })];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: /waiting for the assistant/i })).toBeDisabled();
  });

  it("re-enables sending once the run is no longer active", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.activeChat = makeChat(); // no run
    render(<PromptComposer />);

    const button = screen.getByRole("button", { name: "Send message" });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    expect(mockAppState.sendMessage).toHaveBeenCalledOnce();
  });

  it("blocks sending empty/whitespace-only input even when idle", () => {
    mockAppState.state.draft.text = "   ";
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
});

describe("PromptComposer — Stop control (pre-4H refinement)", () => {
  it("shows the normal Send control while idle", () => {
    render(<PromptComposer />);
    expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop generating" })).not.toBeInTheDocument();
  });

  it("shows Stop instead of Send once a real backend run is active", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Stop generating" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
  });

  it("clicking Stop calls stopActiveRun with the active chat's id", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(mockAppState.stopActiveRun).toHaveBeenCalledExactlyOnceWith("chat-1");
  });

  it("Stop is a real, keyboard-accessible <button> with an appropriate accessible label", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    const stopButton = screen.getByRole("button", { name: "Stop generating" });
    expect(stopButton.tagName).toBe("BUTTON");
    expect(stopButton).toHaveAttribute("type", "button");
    expect(stopButton).toHaveAccessibleName("Stop generating");
  });

  it("keeps the disabled 'waiting' Send affordance (not Stop) for a mock-path pending message with no real backend run", () => {
    mockAppState.activeMessages = [makeMessage({ status: "pending" })];
    render(<PromptComposer />);

    expect(screen.queryByRole("button", { name: "Stop generating" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /waiting for the assistant/i })).toBeDisabled();
  });

  it("Stop never sends a message — sendMessage is not invoked", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(mockAppState.sendMessage).not.toHaveBeenCalled();
  });
});

describe("PromptComposer — resting vs. focus glow (Phase 4G hardening pass)", () => {
  it("never applies the streaming/generating pulse glow, even while a run is active", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    expect(screen.getByTestId("composer-frame").className).not.toContain("anim-intelligence-pulse");
  });

  it("the resting/focus classes are identical whether or not a run is active — streaming never changes the frame's className", () => {
    const { unmount } = render(<PromptComposer />);
    const idleClassName = screen.getByTestId("composer-frame").className;
    unmount();

    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);
    const streamingClassName = screen.getByTestId("composer-frame").className;

    expect(streamingClassName).toBe(idleClassName);
  });

  it("having typed text, with no focus event ever fired, does not add any active/glow class", () => {
    mockAppState.state.draft.text = "hello, this is a typed draft";
    render(<PromptComposer />);

    const frame = screen.getByTestId("composer-frame");
    expect(frame.className).not.toContain("anim-intelligence-pulse");
    // `border-accent` is only ever present behind the `focus-within:`
    // variant (matched separately below) — never as a bare, always-on class.
    expect(frame.className).not.toMatch(/(?<!focus-within:)border-accent/);
  });

  it("the resting state has a plain, subtle border and no shadow/glow classes applied unconditionally", () => {
    render(<PromptComposer />);

    const frame = screen.getByTestId("composer-frame");
    expect(frame.className).toContain("border-subtle/70");
    // The focus glow is expressed ONLY behind the focus-within: variant —
    // never as a bare, always-on utility alongside it.
    expect(frame.className).not.toMatch(/(?<!focus-within:)shadow-\[0_0_0_3px/);
  });

  it("focus is expressed via the real :focus-within CSS variant, present at all times so real keyboard focus is always honored", () => {
    render(<PromptComposer />);

    const frame = screen.getByTestId("composer-frame");
    expect(frame.className).toContain("focus-within:border-accent/40");
    expect(frame.className).toContain("focus-within:shadow-[0_0_0_3px_var(--app-focus-glow)]");
  });

  it("focusing the textarea moves real DOM focus into the composer (the :focus-within trigger)", () => {
    render(<PromptComposer />);

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    // `.focus()`/`.blur()` (not fireEvent.focus/blur) actually move
    // jsdom's document.activeElement, which is what the real
    // `:focus-within` selector — and this assertion — depend on.
    textarea.focus();
    expect(textarea).toHaveFocus();

    textarea.blur();
    expect(textarea).not.toHaveFocus();
  });

  it("no hover-driven class duplicates or approximates the focus treatment", () => {
    render(<PromptComposer />);

    const frame = screen.getByTestId("composer-frame");
    expect(frame.className).not.toMatch(/hover:border-accent/);
    expect(frame.className).not.toMatch(/hover:shadow-\[0_0_0_3px/);
  });

  it("no scale transform is applied to the composer frame at any state", () => {
    const { unmount } = render(<PromptComposer />);
    expect(screen.getByTestId("composer-frame").className).not.toMatch(/\bscale-/);
    unmount();

    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, runStartedAt: Date.now() },
    });
    render(<PromptComposer />);
    expect(screen.getByTestId("composer-frame").className).not.toMatch(/\bscale-/);
  });

  it("the composer frame never references the removed anim-intelligence-pulse class or an isGenerating-style variable in source", () => {
    // Structural guarantee mirroring this codebase's established
    // inspect.getsource() pattern (backend) / ?raw source-import pattern
    // (frontend, see ApprovalCard.test.tsx) -- proves the streaming-driven
    // glow was removed at the source, not merely unreachable today.
    expect(composerSource).not.toContain("anim-intelligence-pulse");
    expect(composerSource).not.toContain("isGenerating");
  });
});
