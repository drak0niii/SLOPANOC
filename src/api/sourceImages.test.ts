import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { getSourceImageContent } from "./sourceImages";

function mockFetchOnce(response: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response as Response));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSourceImageContent — Teams Visual Evidence milestone", () => {
  it("GETs the exact session/source/image content endpoint", async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => blob,
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    const result = await getSourceImageContent("session-1", "source-1", "img-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/sessions/session-1/sources/source-1/images/img-1");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toBe(blob);
  });

  it("URL-encodes session id, source id, and image id independently", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([]),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await getSourceImageContent("session with spaces", "source/weird", "img?id");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `/api/sessions/${encodeURIComponent("session with spaces")}/sources/${encodeURIComponent("source/weird")}/images/${encodeURIComponent("img?id")}`,
    );
  });

  it("forwards the AbortSignal to fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([]),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await getSourceImageContent("session-1", "source-1", "img-1", controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("returns the Blob with its real content type retained", async () => {
    const blob = new Blob([new Uint8Array([9, 9, 9])], { type: "image/jpeg" });
    mockFetchOnce({ ok: true, status: 200, blob: async () => blob });

    const result = await getSourceImageContent("session-1", "source-1", "img-1");
    expect(result.type).toBe("image/jpeg");
  });

  it("maps a non-2xx (unknown source/image, wrong owner) response through the shared SafeError contract, never leaking raw ids", async () => {
    mockFetchOnce({
      ok: false,
      status: 404,
      json: async () => ({
        errorCode: "not_found",
        userMessage: "No visual evidence image was found with that id.",
        retryable: false,
        correlationId: "corr-1",
      }),
    });

    await expect(getSourceImageContent("session-1", "source-1", "missing")).rejects.toMatchObject({
      status: 404,
      errorCode: "not_found",
      message: "No visual evidence image was found with that id.",
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
      await getSourceImageContent("session-1", "source-1", "img-1");
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
    }
  });
});
