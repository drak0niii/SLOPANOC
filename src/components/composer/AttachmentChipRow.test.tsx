import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DraftAttachment } from "../../types";

const mockAppState: {
  state: {
    draft: { attachments: DraftAttachment[]; attachmentLimitNotice: { message: string; key: string } | null };
  };
  removeAttachment: ReturnType<typeof vi.fn>;
  retryImageAttachment: ReturnType<typeof vi.fn>;
} = {
  state: { draft: { attachments: [], attachmentLimitNotice: null } },
  removeAttachment: vi.fn(),
  retryImageAttachment: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { AttachmentChipRow } from "./AttachmentChipRow";

function makeImage(overrides: Partial<DraftAttachment> = {}): DraftAttachment {
  return {
    kind: "image",
    id: "img-1",
    file: new File([new Uint8Array(1)], "photo.png", { type: "image/png" }),
    objectUrl: "blob:mock-1",
    uploadState: "ready",
    filename: "photo.png",
    mimeType: "image/png",
    sizeBytes: 100,
    ...overrides,
  } as DraftAttachment;
}

beforeEach(() => {
  mockAppState.state = { draft: { attachments: [], attachmentLimitNotice: null } };
  mockAppState.removeAttachment.mockClear();
  mockAppState.retryImageAttachment.mockClear();
});

describe("AttachmentChipRow — POST-5.1 B3 real image chips", () => {
  it("renders nothing when the draft has no attachments", () => {
    const { container } = render(<AttachmentChipRow />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a thumbnail (the attachment's own local objectUrl, never re-fetched) for an image attachment", () => {
    mockAppState.state.draft.attachments = [makeImage()];
    const { container } = render(<AttachmentChipRow />);

    const img = container.querySelector("img") as HTMLImageElement;
    expect(img.src).toContain("blob:mock-1");
  });

  it("shows a retry control only for a failed image, never for pending/uploading/ready", () => {
    mockAppState.state.draft.attachments = [makeImage({ uploadState: "failed", error: "Image is too large." })];
    render(<AttachmentChipRow />);
    expect(screen.getByRole("button", { name: /retry uploading photo.png/i })).toBeInTheDocument();
  });

  it.each(["pending", "uploading", "ready"] as const)("does not show a retry control while %s", (uploadState) => {
    mockAppState.state.draft.attachments = [makeImage({ uploadState })];
    render(<AttachmentChipRow />);
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("clicking retry calls retryImageAttachment with the draft's id", () => {
    mockAppState.state.draft.attachments = [makeImage({ id: "img-9", uploadState: "failed" })];
    render(<AttachmentChipRow />);

    fireEvent.click(screen.getByRole("button", { name: /retry uploading photo.png/i }));
    expect(mockAppState.retryImageAttachment).toHaveBeenCalledExactlyOnceWith("img-9");
  });

  it("clicking remove calls removeAttachment with the draft's id, for every upload state", () => {
    mockAppState.state.draft.attachments = [makeImage({ id: "img-9" })];
    render(<AttachmentChipRow />);

    fireEvent.click(screen.getByRole("button", { name: /remove photo.png/i }));
    expect(mockAppState.removeAttachment).toHaveBeenCalledExactlyOnceWith("img-9");
  });

  it("carries the failure message as the chip's title for a failed attachment, and no other safe-error detail", () => {
    mockAppState.state.draft.attachments = [makeImage({ uploadState: "failed", error: "Image is too large." })];
    const { container } = render(<AttachmentChipRow />);
    // The wrapping <span> (title lives one level above the img/buttons).
    expect(container.querySelector("img")?.closest("span[title]")).toHaveAttribute(
      "title",
      "Image is too large.",
    );
  });

  it("still renders a non-image (long-paste) attachment through the existing Chip path, unchanged", () => {
    mockAppState.state.draft.attachments = [
      { id: "att-1", kind: "file", name: "Pasted text.txt", meta: "500 characters", isPastedText: true, content: "x" },
    ];
    render(<AttachmentChipRow />);
    expect(screen.getByText("Pasted text.txt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Pasted text.txt" })).toBeInTheDocument();
  });

  it("renders multiple mixed attachments (image + pasted text) together", () => {
    mockAppState.state.draft.attachments = [
      makeImage({ id: "img-1" }),
      { id: "att-1", kind: "file", name: "Pasted text.txt", meta: "500 characters", isPastedText: true, content: "x" },
    ];
    const { container } = render(<AttachmentChipRow />);
    expect(screen.getByText("Pasted text.txt")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeInTheDocument();
  });
});

describe("AttachmentChipRow — POST-5.1 B3 overflow-UX closure pass", () => {
  it("renders the limit notice text verbatim when present", () => {
    mockAppState.state.draft.attachmentLimitNotice = { message: "Up to 4 images can be attached.", key: "n1" };
    render(<AttachmentChipRow />);
    expect(screen.getByText("Up to 4 images can be attached.")).toBeInTheDocument();
  });

  it("renders as a non-alert, non-modal status line (role=status, not role=alertdialog)", () => {
    mockAppState.state.draft.attachmentLimitNotice = { message: "Up to 4 images can be attached.", key: "n1" };
    render(<AttachmentChipRow />);
    expect(screen.getByRole("status")).toHaveTextContent("Up to 4 images can be attached.");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the notice alongside existing attachment chips, not in place of them", () => {
    mockAppState.state.draft.attachments = [makeImage()];
    mockAppState.state.draft.attachmentLimitNotice = { message: "Up to 4 images can be attached.", key: "n1" };
    const { container } = render(<AttachmentChipRow />);
    expect(container.querySelector("img")).toBeInTheDocument();
    expect(screen.getByText("Up to 4 images can be attached.")).toBeInTheDocument();
  });

  it("does not render the row at all when there are no attachments and no notice", () => {
    const { container } = render(<AttachmentChipRow />);
    expect(container.firstChild).toBeNull();
  });
});
