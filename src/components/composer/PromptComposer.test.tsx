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
  state: {
    draft: { text: string; attachments: unknown[]; sources: unknown[] };
    composerNudgeAt: number;
    workspaceScope: { type: "general" } | { type: "project"; projectId: string };
  };
  activeChat: Chat | null;
  activeMessages: MessageType[];
  setDraftText: ReturnType<typeof vi.fn>;
  addAttachments: ReturnType<typeof vi.fn>;
  queueImageFiles: ReturnType<typeof vi.fn>;
  sendMessage: ReturnType<typeof vi.fn>;
  stopActiveRun: ReturnType<typeof vi.fn>;
} = {
  state: {
    draft: { text: "", attachments: [], sources: [] },
    composerNudgeAt: 0,
    workspaceScope: { type: "general" },
  },
  activeChat: null,
  activeMessages: [],
  setDraftText: vi.fn(),
  addAttachments: vi.fn(),
  queueImageFiles: vi.fn(),
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

function makeImageAttachment(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    kind: "image",
    id: "img-1",
    file: new File(["x"], "screenshot.png", { type: "image/png" }),
    objectUrl: "blob:mock-url",
    uploadState: "ready",
    // POST-5.1 B5 — a genuinely ready-to-send draft image always has a
    // real backend attachmentId by the time uploadState reaches "ready"
    // (see AppState.tsx's UPDATE_DRAFT_IMAGE_ATTACHMENT reducer case).
    attachmentId: "att-1",
    filename: "screenshot.png",
    mimeType: "image/png",
    sizeBytes: 1,
    ...overrides,
  };
}

beforeEach(() => {
  mockAppState.state = {
    draft: { text: "", attachments: [], sources: [] },
    composerNudgeAt: 0,
    workspaceScope: { type: "general" },
  };
  mockAppState.activeChat = null;
  mockAppState.activeMessages = [];
  mockAppState.sendMessage.mockClear();
  mockAppState.stopActiveRun.mockClear();
  mockAppState.addAttachments.mockClear();
  mockAppState.queueImageFiles.mockClear();
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
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
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
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
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
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Stop generating" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
  });

  it("clicking Stop calls stopActiveRun with the active chat's id", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(mockAppState.stopActiveRun).toHaveBeenCalledExactlyOnceWith("chat-1");
  });

  it("Stop is a real, keyboard-accessible <button> with an appropriate accessible label", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
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
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(mockAppState.sendMessage).not.toHaveBeenCalled();
  });
});

describe("PromptComposer — resting vs. focus glow (Phase 4G hardening pass)", () => {
  it("never applies the streaming/generating pulse glow, even while a run is active", () => {
    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
    });
    render(<PromptComposer />);

    expect(screen.getByTestId("composer-frame").className).not.toContain("anim-intelligence-pulse");
  });

  it("the resting/focus classes are identical whether or not a run is active — streaming never changes the frame's className", () => {
    const { unmount } = render(<PromptComposer />);
    const idleClassName = screen.getByTestId("composer-frame").className;
    unmount();

    mockAppState.activeChat = makeChat({
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
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
      run: { runToken: "r1", assistantMessageId: "msg-1", currentActivity: null, activityTrail: [], runStartedAt: Date.now() },
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

describe("PromptComposer — POST-5.1 B5 Send eligibility for real image attachments", () => {
  it.each(["pending", "uploading", "failed"] as const)(
    "disables Send while a draft image attachment is %s, even with typed text present",
    (uploadState) => {
      mockAppState.state.draft.text = "here's a screenshot";
      mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState })];
      render(<PromptComposer />);

      const button = screen.getByRole("button", { name: "Send message" });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(mockAppState.sendMessage).not.toHaveBeenCalled();
    },
  );

  it("enables Send once a draft image attachment is ready, on the real backend (general) branch", () => {
    mockAppState.state.draft.text = "here's a screenshot";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
  });

  it("enables Send for a ready image-only draft (no typed text)", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
  });

  it("still disables Send if a 'ready' image is somehow missing its real backend attachmentId", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment({ attachmentId: undefined })];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("disables Send for a ready image when the workspace is project-scoped (not the real backend branch)", () => {
    mockAppState.state.draft.text = "here's a screenshot";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    mockAppState.state.workspaceScope = { type: "project", projectId: "p1" };
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("disables Send for a ready image when an ad-hoc source is attached (mock read-chat-room branch)", () => {
    mockAppState.state.draft.text = "here's a screenshot";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    mockAppState.state.draft.sources = [{ id: "src-1", kind: "teams_channel", scope: "Ops Bridge" }];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("disables Send for a ready image when the active chat is following a demo script", () => {
    mockAppState.state.draft.text = "here's a screenshot";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    mockAppState.activeChat = makeChat({ demoRun: { scenarioId: "ru-advise", step: 0 } });
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("blocks Enter from submitting while a real image attachment is still uploading", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState: "uploading" })];
    render(<PromptComposer />);

    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    expect(mockAppState.sendMessage).not.toHaveBeenCalled();
  });

  it("Enter submits once a real image attachment is ready", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Enter" });
    expect(mockAppState.sendMessage).toHaveBeenCalled();
  });

  it("re-enables Send once every image attachment has been removed from the draft", () => {
    mockAppState.state.draft.text = "hello";
    mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState: "uploading" })];
    const { rerender } = render(<PromptComposer />);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

    mockAppState.state.draft.attachments = [];
    rerender(<PromptComposer />);
    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
  });

  it("a non-image (pasted-text) attachment alone never blocks Send", () => {
    mockAppState.state.draft.attachments = [
      { id: "att-1", kind: "file", name: "Pasted text.txt", meta: "500 characters", isPastedText: true, content: "x" },
    ];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
  });
});

describe("PromptComposer — POST-5.1 B3 clipboard image paste", () => {
  function pasteImage(textarea: HTMLElement, file: File) {
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [{ kind: "file", type: file.type, getAsFile: () => file }],
        getData: () => "",
      },
    });
  }

  it("routes a pasted image through the central queueImageFiles path, not addAttachments", () => {
    render(<PromptComposer />);
    const file = new File(["binary"], "clip.png", { type: "image/png" });

    pasteImage(screen.getByLabelText("Message"), file);

    expect(mockAppState.queueImageFiles).toHaveBeenCalledExactlyOnceWith([file]);
    expect(mockAppState.addAttachments).not.toHaveBeenCalled();
  });

  it("an image paste is never also treated as a long-text paste, even if clipboard text is also present", () => {
    render(<PromptComposer />);
    const file = new File(["binary"], "clip.png", { type: "image/png" });
    const textarea = screen.getByLabelText("Message");

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [{ kind: "file", type: file.type, getAsFile: () => file }],
        getData: () => "x".repeat(500),
      },
    });

    expect(mockAppState.queueImageFiles).toHaveBeenCalledExactlyOnceWith([file]);
    expect(mockAppState.addAttachments).not.toHaveBeenCalled();
  });

  it("a plain long-text paste (no image) still goes through the existing addAttachments path, unchanged", () => {
    render(<PromptComposer />);
    const textarea = screen.getByLabelText("Message");

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [],
        getData: () => "y".repeat(500),
      },
    });

    expect(mockAppState.addAttachments).toHaveBeenCalledOnce();
    expect(mockAppState.queueImageFiles).not.toHaveBeenCalled();
  });

  it("a short plain-text paste triggers neither path", () => {
    render(<PromptComposer />);
    const textarea = screen.getByLabelText("Message");

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [],
        getData: () => "short",
      },
    });

    expect(mockAppState.addAttachments).not.toHaveBeenCalled();
    expect(mockAppState.queueImageFiles).not.toHaveBeenCalled();
  });
});
