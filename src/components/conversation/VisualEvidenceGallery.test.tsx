import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceVisualEvidenceItemDTO } from "../../api/types";

const getSourceImageContent = vi.fn();
vi.mock("../../api/sourceImages", () => ({
  getSourceImageContent: (...args: unknown[]) => getSourceImageContent(...args),
}));

import { VisualEvidenceSection } from "./VisualEvidenceGallery";

let objectUrlCounter = 0;
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => `blob:mock-${objectUrlCounter++}`;
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}

function item(overrides: Partial<SourceVisualEvidenceItemDTO> = {}): SourceVisualEvidenceItemDTO {
  return {
    image_id: "img-1",
    ordinal: 1,
    mime_type: "image/png",
    size_bytes: 12345,
    author: "Alex",
    sent_at: "2026-09-11T10:00:00Z",
    ...overrides,
  };
}

function pngBlob(): Blob {
  return new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  getSourceImageContent.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("VisualEvidenceSection — empty state", () => {
  it("renders nothing at all for an empty items list", () => {
    const { container } = render(
      <VisualEvidenceSection sessionId="s1" sourceId="src1" items={[]} conversationTitle="Ops Bridge" />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/Visual evidence/)).not.toBeInTheDocument();
  });
});

describe("VisualEvidenceSection — count text", () => {
  it("shows singular '1 image analyzed' for one item", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);
    expect(screen.getByText("Visual evidence · 1 image analyzed")).toBeInTheDocument();
  });

  it("shows plural '3 images analyzed' for three items", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    const items = [1, 2, 3].map((n) => item({ image_id: `img-${n}`, ordinal: n }));
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={items} conversationTitle={null} />);
    expect(screen.getByText("Visual evidence · 3 images analyzed")).toBeInTheDocument();
  });
});

describe("VisualEvidenceSection — ordering", () => {
  it("renders thumbnails in the given (true Teams) order, never re-sorted", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    const items = [1, 2, 3].map((n) => item({ image_id: `img-${n}`, ordinal: n }));
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={items} conversationTitle={null} />);
    await screen.findAllByRole("img");

    const labels = screen.getAllByText(/^Image \d$/).map((el) => el.textContent);
    expect(labels).toEqual(["Image 1", "Image 2", "Image 3"]);
  });
});

describe("VisualEvidenceSection — lazy fetch lifecycle", () => {
  it("shows a loading state, then fetches lazily via getSourceImageContent with the correct ids", async () => {
    const pending = deferred<Blob>();
    getSourceImageContent.mockReturnValue(pending.promise);

    render(
      <VisualEvidenceSection sessionId="session-1" sourceId="source-1" items={[item({ image_id: "img-7" })]} conversationTitle={null} />,
    );

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(getSourceImageContent).toHaveBeenCalledWith("session-1", "source-1", "img-7", expect.any(AbortSignal));

    await act(async () => {
      pending.resolve(pngBlob());
      await Promise.resolve();
    });
  });

  it("creates exactly one object URL per thumbnail once loaded", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    const createSpy = vi.spyOn(URL, "createObjectURL");

    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);

    const img = await screen.findByRole("img");
    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(img).toHaveAttribute("src", expect.stringContaining("blob:"));
  });

  it("revokes the object URL on unmount", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL");

    const { unmount } = render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);
    await screen.findByRole("img");
    unmount();

    expect(revokeSpy).toHaveBeenCalled();
  });
});

describe("VisualEvidenceSection — error/unavailable state", () => {
  it("shows 'Image unavailable' + Retry on a fetch failure, never crashing the section", async () => {
    getSourceImageContent.mockRejectedValue(new Error("gone"));

    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);

    expect(await screen.findByText("Image unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("retry re-fetches and can succeed after an initial failure", async () => {
    getSourceImageContent.mockRejectedValueOnce(new Error("first attempt fails")).mockResolvedValueOnce(pngBlob());

    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);
    await screen.findByText("Image unavailable");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByRole("img");
    expect(getSourceImageContent).toHaveBeenCalledTimes(2);
  });

  it("one image's failure never affects a sibling image", async () => {
    getSourceImageContent.mockImplementation(async (_s: string, _src: string, imageId: string) => {
      if (imageId === "img-bad") throw new Error("fails");
      return pngBlob();
    });

    const items = [item({ image_id: "img-good", ordinal: 1 }), item({ image_id: "img-bad", ordinal: 2 })];
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={items} conversationTitle={null} />);

    await screen.findByRole("img");
    await screen.findByText("Image unavailable");
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});

describe("VisualEvidenceSection — click-to-preview", () => {
  it("clicking a thumbnail opens a preview with 'Image N of M', sender, timestamp, and conversation title", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    const items = [
      item({ image_id: "img-1", ordinal: 1, author: "Priya", sent_at: "2026-09-11T09:00:00Z" }),
      item({ image_id: "img-2", ordinal: 2 }),
    ];
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={items} conversationTitle="Ops Bridge" />);

    const thumbnails = await screen.findAllByRole("img");
    fireEvent.click(thumbnails[0].closest("button")!);

    expect(await screen.findByText("Image 1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/Priya/)).toBeInTheDocument();
    expect(screen.getByText(/Ops Bridge/)).toBeInTheDocument();
  });

  it("does not refetch when opening the preview — reuses the already-fetched object URL", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);

    const thumbnail = await screen.findByRole("img");
    const thumbnailSrc = thumbnail.getAttribute("src");
    fireEvent.click(thumbnail.closest("button")!);

    // Radix Dialog correctly `aria-hides` the rest of the page while open,
    // so the (still-mounted) thumbnail <img> drops out of the accessible
    // tree — only the dialog's own preview <img> remains query-able. The
    // real proof of "reused, never refetched" is: exactly one fetch call,
    // and the preview's src is the SAME object URL the thumbnail already
    // held before the dialog opened.
    const previewImg = await screen.findByRole("img");
    expect(getSourceImageContent).toHaveBeenCalledTimes(1);
    expect(previewImg.getAttribute("src")).toBe(thumbnailSrc);
  });

  it("Escape closes the preview dialog", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);

    const thumbnail = await screen.findByRole("img");
    fireEvent.click(thumbnail.closest("button")!);
    await screen.findByText(/Image 1 of 1/);

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByText(/Image 1 of 1/)).not.toBeInTheDocument();
    });
  });

  it("the close (X) button closes the preview dialog", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);

    const thumbnail = await screen.findByRole("img");
    fireEvent.click(thumbnail.closest("button")!);
    await screen.findByText(/Image 1 of 1/);

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    await waitFor(() => {
      expect(screen.queryByText(/Image 1 of 1/)).not.toBeInTheDocument();
    });
  });
});

describe("VisualEvidenceSection — never requires raw ids", () => {
  it("the component's own props never include a chat_id/message_id/hosted_content_id field", async () => {
    getSourceImageContent.mockResolvedValue(pngBlob());
    render(<VisualEvidenceSection sessionId="s1" sourceId="src1" items={[item()]} conversationTitle={null} />);
    await screen.findByRole("img");

    // Structural proof: getSourceImageContent was called with only
    // opaque ids (session/source/image), never anything Teams-shaped.
    const [sessionArg, sourceArg, imageArg] = getSourceImageContent.mock.calls[0];
    expect(sessionArg).toBe("s1");
    expect(sourceArg).toBe("src1");
    expect(imageArg).toBe("img-1");
  });
});
