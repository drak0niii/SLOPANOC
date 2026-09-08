import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chat, DraftAttachment, DraftImageAttachment, Message as MessageType } from "../../types";

/**
 * POST-B7 UI/UX refinement (Item 5) — attachment/text formatting
 * separation. AUDIT CONCLUSION (see this pass's own final report): the
 * composer already structurally separates `SourceChipRow`,
 * `AttachmentChipRow`, and the `<textarea>` as independent sibling
 * elements under one padded container — no `contenteditable`, no inline
 * attachment tokens inside the text, no shared/inherited typographic
 * classes. No code change was made for this item; these tests exist to
 * LOCK IN that already-correct structural separation as a regression
 * guard, using the REAL `AttachmentChipRow` (unlike PromptComposer.test.tsx,
 * which stubs it out to focus on send-gating logic instead).
 */
vi.mock("./ComposerPlusMenu", () => ({ ComposerPlusMenu: () => null }));
vi.mock("./ThinkingEffortSelector", () => ({ ThinkingEffortSelector: () => null }));
vi.mock("./SkillSelector", () => ({ SkillSelector: () => null }));
vi.mock("./MicrophoneButton", () => ({ MicrophoneButton: () => null }));
vi.mock("./SourceChipRow", () => ({ SourceChipRow: () => null }));

const mockAppState: {
  state: {
    draft: {
      text: string;
      attachments: DraftAttachment[];
      sources: unknown[];
      attachmentLimitNotice: { message: string; key: string } | null;
    };
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
  removeAttachment: ReturnType<typeof vi.fn>;
  retryImageAttachment: ReturnType<typeof vi.fn>;
} = {
  state: {
    draft: { text: "", attachments: [], sources: [], attachmentLimitNotice: null },
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
  removeAttachment: vi.fn(),
  retryImageAttachment: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { PromptComposer } from "./PromptComposer";

function makeImageAttachment(overrides: Partial<DraftImageAttachment> = {}): DraftImageAttachment {
  return {
    kind: "image",
    id: "img-1",
    file: new File([new Uint8Array(1)], "screenshot.png", { type: "image/png" }),
    objectUrl: "blob:mock-1",
    uploadState: "ready",
    attachmentId: "att-1",
    filename: "screenshot.png",
    mimeType: "image/png",
    sizeBytes: 100,
    ...overrides,
  } as DraftImageAttachment;
}

beforeEach(() => {
  mockAppState.state = {
    draft: { text: "", attachments: [], sources: [], attachmentLimitNotice: null },
    composerNudgeAt: 0,
    workspaceScope: { type: "general" },
  };
  mockAppState.activeChat = null;
  mockAppState.activeMessages = [];
  mockAppState.setDraftText.mockClear();
  mockAppState.addAttachments.mockClear();
  mockAppState.queueImageFiles.mockClear();
  mockAppState.sendMessage.mockClear();
  mockAppState.removeAttachment.mockClear();
  mockAppState.retryImageAttachment.mockClear();
});

function textarea(): HTMLTextAreaElement {
  return screen.getByLabelText("Message") as HTMLTextAreaElement;
}

describe("PromptComposer — attachment/text structural separation (POST-B7 Item 5)", () => {
  it("1. an attachment present at mount does not affect the textarea's own classes", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    const el = textarea();
    expect(el.className).toContain("text-base");
    expect(el.className).toContain("text-primary");
    expect(el.className).not.toMatch(/attachment/i);
  });

  it("2. typing after an attachment already exists updates only the draft text, never the attachment", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    fireEvent.change(textarea(), { target: { value: "here is context" } });

    expect(mockAppState.setDraftText).toHaveBeenCalledExactlyOnceWith("here is context");
    expect(mockAppState.removeAttachment).not.toHaveBeenCalled();
    expect(mockAppState.addAttachments).not.toHaveBeenCalled();
  });

  it("3. pasting an image, then continuing to type, keeps typed text as a completely separate action", () => {
    render(<PromptComposer />);
    const el = textarea();
    const file = new File(["binary"], "clip.png", { type: "image/png" });

    fireEvent.paste(el, {
      clipboardData: { items: [{ kind: "file", type: file.type, getAsFile: () => file }], getData: () => "" },
    });
    expect(mockAppState.queueImageFiles).toHaveBeenCalledExactlyOnceWith([file]);

    fireEvent.change(el, { target: { value: "after the paste" } });
    expect(mockAppState.setDraftText).toHaveBeenCalledExactlyOnceWith("after the paste");
  });

  it("4. removing an attachment (via AttachmentChipRow's own real remove button) never touches draft text", () => {
    mockAppState.state.draft.text = "keep this text";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Remove screenshot.png" }));

    expect(mockAppState.removeAttachment).toHaveBeenCalledExactlyOnceWith("img-1");
    expect(mockAppState.setDraftText).not.toHaveBeenCalled();
    expect(textarea().value).toBe("keep this text");
  });

  it("5. retrying a failed upload (AttachmentChipRow's own real retry button) never touches draft text", () => {
    mockAppState.state.draft.text = "keep this text";
    mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState: "failed", error: "boom" })];
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Retry uploading screenshot.png" }));

    expect(mockAppState.retryImageAttachment).toHaveBeenCalledExactlyOnceWith("img-1");
    expect(mockAppState.setDraftText).not.toHaveBeenCalled();
    expect(textarea().value).toBe("keep this text");
  });

  it("6. sending with both an image and typed text goes through the one real sendMessage call — both preserved in state", () => {
    mockAppState.state.draft.text = "here's a screenshot";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(mockAppState.sendMessage).toHaveBeenCalledOnce();
    // sendMessage reads current draft state itself — this proves the
    // composer never mutated/cleared it before calling send.
    expect(mockAppState.state.draft.text).toBe("here's a screenshot");
    expect(mockAppState.state.draft.attachments).toHaveLength(1);
  });

  it("7. the textarea remains a real, editable, non-disabled control while an attachment exists", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    const el = textarea();
    expect(el.tagName).toBe("TEXTAREA");
    expect(el).not.toBeDisabled();
    expect(el).not.toHaveAttribute("readonly");
  });

  it("8. normal focus/selection still works on the textarea while an attachment exists", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    mockAppState.state.draft.text = "some text";
    render(<PromptComposer />);

    const el = textarea();
    el.focus();
    expect(el).toHaveFocus();
    el.setSelectionRange(0, 4);
    expect(el.selectionStart).toBe(0);
    expect(el.selectionEnd).toBe(4);
  });

  it("9. multiple attachments never wrap the textarea in attachment-specific styling", () => {
    mockAppState.state.draft.attachments = [
      makeImageAttachment({ id: "img-1", filename: "one.png" }),
      makeImageAttachment({ id: "img-2", filename: "two.png" }),
      makeImageAttachment({ id: "img-3", filename: "three.png" }),
    ];
    render(<PromptComposer />);

    // All three chips render, independently of the textarea.
    expect(screen.getByText("one.png")).toBeInTheDocument();
    expect(screen.getByText("two.png")).toBeInTheDocument();
    expect(screen.getByText("three.png")).toBeInTheDocument();
    expect(textarea().className).not.toMatch(/attachment|chip/i);
  });

  it("10. clearing all attachments leaves the typed text completely unchanged", () => {
    mockAppState.state.draft.text = "unchanged text";
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    const { rerender } = render(<PromptComposer />);
    expect(textarea().value).toBe("unchanged text");

    mockAppState.state.draft.attachments = [];
    rerender(<PromptComposer />);
    expect(textarea().value).toBe("unchanged text");
  });

  it("11. a text-only composer (no attachments) renders no attachment chip row at all", () => {
    mockAppState.state.draft.text = "just text";
    render(<PromptComposer />);

    expect(screen.queryByRole("button", { name: /^Remove /i })).not.toBeInTheDocument();
    expect(textarea().value).toBe("just text");
  });

  it("12. an attachment-only send (no typed text) is unaffected by this structural separation", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(mockAppState.sendMessage).toHaveBeenCalledOnce();
  });

  it("13/14. the textarea is the single source of truth for draft text — no duplicate or lost text across an attachment lifecycle", () => {
    mockAppState.state.draft.text = "draft in progress";
    mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState: "uploading" })];
    const { rerender } = render(<PromptComposer />);
    expect(textarea().value).toBe("draft in progress");

    mockAppState.state.draft.attachments = [makeImageAttachment({ uploadState: "ready" })];
    rerender(<PromptComposer />);
    // Still exactly the same single string — never duplicated, never lost.
    expect(textarea().value).toBe("draft in progress");
  });

  it("attachments and the textarea are independent DOM siblings, not a shared contenteditable tree", () => {
    mockAppState.state.draft.attachments = [makeImageAttachment()];
    render(<PromptComposer />);

    const el = textarea();
    expect(el.getAttribute("contenteditable")).toBeNull();
    // The attachment chip row is a sibling, not an ancestor/descendant of the textarea.
    const chip = screen.getByText("screenshot.png");
    expect(chip.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(el.contains(chip)).toBe(false);
    expect(chip.contains(el)).toBe(false);
  });
});
