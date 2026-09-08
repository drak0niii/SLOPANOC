import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PersistedAttachmentReference } from "../../types";

const getAttachmentContent = vi.fn();
vi.mock("../../api/attachments", () => ({
  getAttachmentContent: (...args: unknown[]) => getAttachmentContent(...args),
}));

import { PersistedImageAttachment } from "./PersistedImageAttachment";

// jsdom does not implement these — same minimal polyfill pattern already
// used by AppState.attachments.test.tsx for B3 draft images.
let objectUrlCounter = 0;
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => `blob:mock-${objectUrlCounter++}`;
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}

function reference(overrides: Partial<PersistedAttachmentReference> = {}): PersistedAttachmentReference {
  return {
    attachmentId: "att-1",
    filename: "screenshot.png",
    mimeType: "image/png",
    sizeBytes: 12345,
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
  getAttachmentContent.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PersistedImageAttachment — loading", () => {
  it("shows a loading placeholder with the filename, no <img>, while the content GET is in flight", async () => {
    const pending = deferred<Blob>();
    getAttachmentContent.mockReturnValue(pending.promise);

    render(<PersistedImageAttachment reference={reference()} />);

    expect(screen.getByText("screenshot.png")).toBeInTheDocument();
    expect(screen.getByText("Loading image…")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();

    await act(async () => {
      pending.resolve(pngBlob());
      await Promise.resolve();
    });
  });
});

describe("PersistedImageAttachment — success", () => {
  it("creates exactly one object URL and renders it as the <img> src, with a filename-based alt", async () => {
    getAttachmentContent.mockResolvedValue(pngBlob());
    const createSpy = vi.spyOn(URL, "createObjectURL");

    render(<PersistedImageAttachment reference={reference({ filename: "diagram.png" })} />);

    const img = await screen.findByRole("img");
    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(img).toHaveAttribute("src", expect.stringContaining("blob:"));
    expect(img).toHaveAttribute("alt", "Attached image: diagram.png");
    // No base64/data URL ever generated.
    expect(img.getAttribute("src")).not.toMatch(/^data:/);
  });
});

describe("PersistedImageAttachment — cleanup", () => {
  it("aborts the in-flight request on unmount and never installs a stale image", async () => {
    const pending = deferred<Blob>();
    let capturedSignal: AbortSignal | undefined;
    getAttachmentContent.mockImplementation((_id: string, signal?: AbortSignal) => {
      capturedSignal = signal;
      return pending.promise;
    });

    const { unmount } = render(<PersistedImageAttachment reference={reference()} />);
    expect(capturedSignal?.aborted).toBe(false);

    unmount();
    expect(capturedSignal?.aborted).toBe(true);

    // The late response resolving after unmount must not throw or update
    // anything — resolving it here just proves no crash occurs.
    await act(async () => {
      pending.resolve(pngBlob());
      await Promise.resolve();
    });
  });

  it("revokes the object URL on unmount after a successful load", async () => {
    getAttachmentContent.mockResolvedValue(pngBlob());
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const { unmount } = render(<PersistedImageAttachment reference={reference()} />);
    await screen.findByRole("img");

    unmount();
    expect(revokeSpy).toHaveBeenCalledTimes(1);
  });

  it("a reference change aborts the old request, revokes the old URL, and fetches the new reference", async () => {
    const first = deferred<Blob>();
    const second = deferred<Blob>();
    let callCount = 0;
    const signals: (AbortSignal | undefined)[] = [];
    getAttachmentContent.mockImplementation((_id: string, signal?: AbortSignal) => {
      callCount += 1;
      signals.push(signal);
      return callCount === 1 ? first.promise : second.promise;
    });
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const { rerender } = render(<PersistedImageAttachment reference={reference({ attachmentId: "att-1" })} />);
    await act(async () => {
      first.resolve(pngBlob());
      await Promise.resolve();
    });
    await screen.findByRole("img");

    rerender(<PersistedImageAttachment reference={reference({ attachmentId: "att-2", filename: "second.png" })} />);
    expect(signals[0]?.aborted).toBe(true);
    expect(callCount).toBe(2);

    await act(async () => {
      second.resolve(pngBlob());
      await Promise.resolve();
    });
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("alt", "Attached image: second.png");
    expect(revokeSpy).toHaveBeenCalled(); // the FIRST reference's URL was revoked when replaced.
  });

  it("a late (superseded) response cannot overwrite the newer image", async () => {
    const first = deferred<Blob>();
    const second = deferred<Blob>();
    let callCount = 0;
    getAttachmentContent.mockImplementation(() => {
      callCount += 1;
      return callCount === 1 ? first.promise : second.promise;
    });

    const { rerender } = render(<PersistedImageAttachment reference={reference({ attachmentId: "att-1" })} />);
    rerender(<PersistedImageAttachment reference={reference({ attachmentId: "att-2", filename: "second.png" })} />);

    await act(async () => {
      second.resolve(pngBlob());
      await Promise.resolve();
    });
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("alt", "Attached image: second.png");

    // The FIRST (stale) request now resolves — must not replace the image.
    await act(async () => {
      first.resolve(pngBlob());
      await Promise.resolve();
    });
    expect(screen.getByRole("img")).toHaveAttribute("alt", "Attached image: second.png");
  });

  it("StrictMode double-invocation does not leak object URLs or crash", async () => {
    getAttachmentContent.mockResolvedValue(pngBlob());
    const createSpy = vi.spyOn(URL, "createObjectURL");

    render(
      <StrictMode>
        <PersistedImageAttachment reference={reference()} />
      </StrictMode>,
    );

    await screen.findByRole("img");
    // Exactly one REAL object URL is ever installed for the settled mount
    // (StrictMode's discarded first-pass effect is aborted before it can
    // create one — see the "cancelled" guard in the component itself).
    expect(createSpy).toHaveBeenCalledTimes(1);
  });
});

describe("PersistedImageAttachment — failure / retry", () => {
  it("a content fetch failure shows a safe unavailable state, never a raw error", async () => {
    getAttachmentContent.mockRejectedValue(new Error("500 Internal Server Error"));

    render(<PersistedImageAttachment reference={reference()} />);

    await waitFor(() => expect(screen.getByText("Image unavailable")).toBeInTheDocument());
    expect(screen.queryByText(/500|Internal Server Error/)).not.toBeInTheDocument();
    expect(screen.getByText("screenshot.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("Retry issues a second GET and renders the image on success", async () => {
    getAttachmentContent.mockRejectedValueOnce(new Error("boom"));
    render(<PersistedImageAttachment reference={reference()} />);
    await waitFor(() => expect(screen.getByText("Image unavailable")).toBeInTheDocument());

    getAttachmentContent.mockResolvedValueOnce(pngBlob());
    await act(async () => {
      screen.getByRole("button", { name: "Retry" }).click();
      await Promise.resolve();
    });

    await screen.findByRole("img");
    expect(getAttachmentContent).toHaveBeenCalledTimes(2);
  });

  it("repeated failures do not trigger an automatic retry loop", async () => {
    getAttachmentContent.mockRejectedValue(new Error("boom"));
    render(<PersistedImageAttachment reference={reference()} />);
    await waitFor(() => expect(screen.getByText("Image unavailable")).toBeInTheDocument());

    // No automatic re-fetch merely from time passing / re-renders.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(getAttachmentContent).toHaveBeenCalledTimes(1);
  });
});

describe("PersistedImageAttachment — unsupported MIME", () => {
  it("an unsupported mimeType (e.g. image/svg+xml) never fetches and shows a safe unsupported state", async () => {
    render(<PersistedImageAttachment reference={reference({ mimeType: "image/svg+xml" })} />);

    expect(screen.getByText("Unsupported format")).toBeInTheDocument();
    expect(getAttachmentContent).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});

describe("PersistedImageAttachment — multiple instances", () => {
  it("one image's failure does not affect a sibling instance's success", async () => {
    getAttachmentContent.mockImplementation((id: string) =>
      id === "att-fail" ? Promise.reject(new Error("boom")) : Promise.resolve(pngBlob()),
    );

    render(
      <>
        <PersistedImageAttachment reference={reference({ attachmentId: "att-fail", filename: "bad.png" })} />
        <PersistedImageAttachment reference={reference({ attachmentId: "att-ok", filename: "good.png" })} />
      </>,
    );

    await waitFor(() => expect(screen.getByText("Image unavailable")).toBeInTheDocument());
    const img = await screen.findByRole("img");
    expect(img).toHaveAttribute("alt", "Attached image: good.png");
  });
});
