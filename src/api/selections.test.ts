import { afterEach, describe, expect, it, vi } from "vitest";
import { chooseSelection, skipSelection } from "./selections";

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

describe("chooseSelection", () => {
  it("POSTs to the exact choose path with the exact session/selection ids and option_id body", async () => {
    mockFetchJson({
      session_id: "s1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: null,
    });

    await chooseSelection("s1", "sel1", "opt1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/selections/sel1/choose");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ option_id: "opt1" });
  });

  it("returns the parsed response, including pending_action when present", async () => {
    const body = {
      session_id: "s1",
      selection_id: "sel1",
      status: "resolved",
      selected_label: "Project Falcon Room Test",
      pending_action: { proposal_id: "p1", operation: "teams.sendMessage", status: "pending" },
    };
    mockFetchJson(body);

    const response = await chooseSelection("s1", "sel1", "opt1");
    expect(response).toEqual(body);
  });

  it("URL-encodes both the session id and the selection id", async () => {
    mockFetchJson({ session_id: "s/1", selection_id: "sel/1", status: "resolved", selected_label: "X", pending_action: null });
    await chooseSelection("s/1", "sel/1", "opt1");
    const [url] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s%2F1/selections/sel%2F1/choose");
  });
});

describe("skipSelection", () => {
  it("POSTs to the exact skip path with no request body", async () => {
    mockFetchJson({ session_id: "s1", selection_id: "sel1", status: "skipped" });

    await skipSelection("s1", "sel1");

    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/sessions/s1/selections/sel1/skip");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("returns the parsed response", async () => {
    const body = { session_id: "s1", selection_id: "sel1", status: "skipped" };
    mockFetchJson(body);

    const response = await skipSelection("s1", "sel1");
    expect(response).toEqual(body);
  });
});
