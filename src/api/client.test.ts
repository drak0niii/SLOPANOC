import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, postJson } from "./client";

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

describe("postJson — SafeError body parsing", () => {
  it("uses the real backend userMessage/errorCode/retryable/correlationId when the body is SafeError-shaped", async () => {
    mockFetchFailure(409, {
      errorCode: "action_failure",
      userMessage: "This proposal has expired.",
      retryable: false,
      correlationId: "corr-1",
    });

    await expect(postJson("/api/whatever")).rejects.toMatchObject({
      message: "This proposal has expired.",
      status: 409,
      errorCode: "action_failure",
      retryable: false,
      correlationId: "corr-1",
    });
  });

  it("populates the structured reason field when present (Phase 4G)", async () => {
    mockFetchFailure(409, {
      errorCode: "action_failure",
      userMessage: "This proposal has expired.",
      retryable: false,
      correlationId: "corr-1",
      reason: "proposal_expired",
    });

    try {
      await postJson("/api/whatever");
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).reason).toBe("proposal_expired");
    }
  });

  it("falls back to the generic message when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError("not json");
        },
      } as unknown as Response),
    );

    try {
      await postJson("/api/whatever");
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      const apiError = error as ApiError;
      expect(apiError.message).toBe("The request could not be completed. Please try again.");
      expect(apiError.errorCode).toBeUndefined();
      expect(apiError.reason).toBeUndefined();
    }
  });

  it("falls back to the generic message when the body is JSON but not SafeError-shaped", async () => {
    mockFetchFailure(500, { detail: "some other shape entirely" });

    try {
      await postJson("/api/whatever");
      expect.unreachable();
    } catch (error) {
      const apiError = error as ApiError;
      expect(apiError.message).toBe("The request could not be completed. Please try again.");
      expect(apiError.errorCode).toBeUndefined();
    }
  });

  it("still resolves normally on a successful (ok) response — unaffected by the error-parsing change", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ session_id: "s1" }),
      } as unknown as Response),
    );

    const result = await postJson<{ session_id: string }>("/api/sessions");
    expect(result).toEqual({ session_id: "s1" });
  });
});
