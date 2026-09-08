import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { getAttachmentContent, uploadAttachment } from "./attachments";

function mockFetchOnce(response: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response as Response));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadAttachment — POST-5.1 B3 real attachment API client", () => {
  it("posts a multipart FormData with the field name 'file' to the session's attachment route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        attachment_id: "att-1",
        filename: "photo.png",
        mime_type: "image/png",
        size_bytes: 100,
        status: "ready",
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    const file = new File([new Uint8Array(10)], "photo.png", { type: "image/png" });
    const result = await uploadAttachment("session-1", file);

    expect(result).toEqual({
      attachment_id: "att-1",
      filename: "photo.png",
      mime_type: "image/png",
      size_bytes: 100,
      status: "ready",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/sessions/session-1/attachments");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    // Never set manually — the browser must compute the multipart boundary
    // itself, or the backend's multipart parser rejects the request.
    expect(init.headers).toBeUndefined();
  });

  it("URL-encodes the session id into the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ attachment_id: "a", filename: "f.png", mime_type: "image/png", size_bytes: 1, status: "ready" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await uploadAttachment("session with spaces", new File([new Uint8Array(1)], "f.png", { type: "image/png" }));

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain(encodeURIComponent("session with spaces"));
  });

  it("passes the AbortSignal through to fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ attachment_id: "a", filename: "f.png", mime_type: "image/png", size_bytes: 1, status: "ready" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await uploadAttachment("session-1", new File([new Uint8Array(1)], "f.png", { type: "image/png" }), controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("maps a non-2xx response through the same SafeError contract every other endpoint uses", async () => {
    mockFetchOnce({
      ok: false,
      status: 413,
      json: async () => ({
        errorCode: "payload_too_large",
        userMessage: "The uploaded image is too large.",
        retryable: false,
        correlationId: "corr-1",
      }),
    });

    await expect(
      uploadAttachment("session-1", new File([new Uint8Array(1)], "f.png", { type: "image/png" })),
    ).rejects.toMatchObject({
      status: 413,
      errorCode: "payload_too_large",
    });
  });

  it("throws ApiError (not a raw fetch/network error) on transport failure", async () => {
    mockFetchOnce({ ok: false, status: 500, json: async () => ({ detail: "unstructured" }) });

    try {
      await uploadAttachment("session-1", new File([new Uint8Array(1)], "f.png", { type: "image/png" }));
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
    }
  });
});

describe("getAttachmentContent — POST-5.1 B4D persisted binary fetch", () => {
  it("GETs the exact content endpoint for the given attachment id", async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => blob,
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    const result = await getAttachmentContent("att-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/attachments/att-1/content");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toBe(blob);
  });

  it("URL-encodes the attachment id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([]),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await getAttachmentContent("att with spaces");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/attachments/${encodeURIComponent("att with spaces")}/content`);
  });

  it("forwards the AbortSignal to fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([]),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getAttachmentContent("att-1", controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("returns the Blob for a successful response, content type retained", async () => {
    const blob = new Blob([new Uint8Array([9, 9, 9])], { type: "image/webp" });
    mockFetchOnce({ ok: true, status: 200, blob: async () => blob });

    const result = await getAttachmentContent("att-1");
    expect(result.type).toBe("image/webp");
  });

  it("maps a non-2xx (unknown/foreign-owner attachment) response through the shared SafeError contract", async () => {
    mockFetchOnce({
      ok: false,
      status: 404,
      json: async () => ({
        errorCode: "not_found",
        userMessage: "No such attachment was found.",
        retryable: false,
        correlationId: "corr-1",
      }),
    });

    await expect(getAttachmentContent("missing")).rejects.toMatchObject({
      status: 404,
      errorCode: "not_found",
      message: "No such attachment was found.",
    });
  });

  it("a non-JSON failure body still maps to a generic, safe ApiError — never a raw response leak", async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError("not json");
      },
    });

    try {
      await getAttachmentContent("att-1");
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).message).toBe("The request could not be completed. Please try again.");
    }
  });
});
