import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { uploadAttachment } from "./attachments";

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
