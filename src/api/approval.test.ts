import { afterEach, describe, expect, it, vi } from "vitest";
import { approveAction, executeApprovedAction, rejectAction } from "./approval";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("approveAction", () => {
  it("POSTs to the exact approve path with the exact session/proposal ids", async () => {
    mockFetchJson({ session_id: "s1", result: "approved", pending_action: null });

    await approveAction("s1", "p1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ proposal_id: "p1" });
  });

  it("returns the parsed response", async () => {
    const body = { session_id: "s1", result: "approved", pending_action: { proposal_id: "p1", status: "approved" } };
    mockFetchJson(body);

    const response = await approveAction("s1", "p1");
    expect(response).toEqual(body);
  });

  it("URL-encodes the session id", async () => {
    mockFetchJson({ session_id: "s/1", result: "approved", pending_action: null });
    await approveAction("s/1", "p1");
    const [url] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s%2F1/approve");
  });
});

describe("rejectAction", () => {
  it("POSTs to the exact reject path with the exact session/proposal ids", async () => {
    mockFetchJson({ session_id: "s1", result: "rejected", pending_action: null });

    await rejectAction("s1", "p1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/reject");
    expect(JSON.parse(init.body)).toEqual({ proposal_id: "p1" });
  });
});

describe("executeApprovedAction", () => {
  it("POSTs to the exact execute path with the exact session/proposal ids", async () => {
    mockFetchJson({ session_id: "s1", result: "executed", pending_action: null, executed_action: null });

    await executeApprovedAction("s1", "p1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/execute");
    expect(JSON.parse(init.body)).toEqual({ proposal_id: "p1" });
  });

  it("returns the parsed executed_action", async () => {
    const body = {
      session_id: "s1",
      result: "executed",
      pending_action: null,
      executed_action: { chat_id: "c1", title: "Ops Bridge", web_url: "https://teams/x" },
    };
    mockFetchJson(body);

    const response = await executeApprovedAction("s1", "p1");
    expect(response.executed_action).toEqual(body.executed_action);
  });
});
