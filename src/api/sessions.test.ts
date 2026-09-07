import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { getSessionHistory, listSavedSessions, renameSession } from "./sessions";

function mockFetchJson(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    } as unknown as Response),
  );
}

function mockFetchFailure(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status,
      json: async () => body,
    } as unknown as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listSavedSessions", () => {
  it("GETs the exact saved-chat list path with no body", async () => {
    mockFetchJson({ sessions: [] });

    await listSavedSessions();

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("returns the parsed sessions array verbatim", async () => {
    const body = {
      sessions: [
        { session_id: "s1", title: "B4B Live Persistence Test", updated_at: "2026-09-07T22:25:09.620902+00:00" },
      ],
    };
    mockFetchJson(body);

    const response = await listSavedSessions();
    expect(response).toEqual(body);
  });

  it("maps a SafeError failure the same way every other endpoint does", async () => {
    mockFetchFailure(500, {
      errorCode: "internal_error",
      userMessage: "Something went wrong. Please try again.",
      retryable: true,
      correlationId: "corr-1",
    });

    await expect(listSavedSessions()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("getSessionHistory", () => {
  it("GETs the exact history path for the given session id", async () => {
    mockFetchJson({ session_id: "s1", messages: [] });

    await getSessionHistory("s1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/history");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("URL-encodes the session id", async () => {
    mockFetchJson({ session_id: "s/1", messages: [] });
    await getSessionHistory("s/1");
    const [url] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s%2F1/history");
  });

  it("returns the parsed messages verbatim, including attachments", async () => {
    const body = {
      session_id: "s1",
      messages: [
        {
          message_id: "e-1:user",
          turn_id: "e-1",
          role: "user",
          text: "hi",
          created_at: "2026-09-07T22:25:09.620902+00:00",
          attachments: [],
        },
        {
          message_id: "e-1:assistant",
          turn_id: "e-1",
          role: "assistant",
          text: "B4B smoke confirmed.",
          created_at: "2026-09-07T22:25:12.382673+00:00",
          attachments: [],
        },
      ],
    };
    mockFetchJson(body);

    const response = await getSessionHistory("s1");
    expect(response).toEqual(body);
  });

  it("maps a SafeError failure the same way every other endpoint does", async () => {
    mockFetchFailure(404, {
      errorCode: "not_found",
      userMessage: "This conversation could not be found.",
      retryable: false,
      correlationId: "corr-2",
    });

    await expect(getSessionHistory("missing")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("renameSession", () => {
  it("PATCHes the exact session path with the title body", async () => {
    mockFetchJson({ session_id: "s1", title: "New title", updated_at: "2026-09-07T22:25:09.620902+00:00" });

    await renameSession("s1", "New title");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ title: "New title" });
  });

  it("URL-encodes the session id", async () => {
    mockFetchJson({ session_id: "s/1", title: "New title", updated_at: "2026-09-07T22:25:09.620902+00:00" });
    await renameSession("s/1", "New title");
    const [url] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s%2F1");
  });

  it("returns the parsed response, including the server-echoed title", async () => {
    const body = { session_id: "s1", title: "B4C UI Rename Test", updated_at: "2026-09-07T22:25:09.620902+00:00" };
    mockFetchJson(body);

    const response = await renameSession("s1", "B4C UI Rename Test");
    expect(response).toEqual(body);
  });

  it("maps a SafeError failure the same way every other endpoint does", async () => {
    mockFetchFailure(409, {
      errorCode: "conflict",
      userMessage: "This chat could not be renamed right now. Please try again.",
      retryable: true,
      correlationId: "corr-3",
    });

    await expect(renameSession("s1", "New title")).rejects.toBeInstanceOf(ApiError);
  });
});
