import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const createSession = vi.fn(async () => ({ session_id: "session-1" }));
vi.mock("../api/sessions", () => ({ createSession: () => createSession() }));

const runBackendChat = vi.fn();
vi.mock("../api/runBackendChat", () => ({ runBackendChat: (...args: unknown[]) => runBackendChat(...args) }));

import { AppStateProvider, useAppState } from "./AppState";

let latest: ReturnType<typeof useAppState>;
function Harness() {
  latest = useAppState();
  return null;
}

describe("AppState — AbortController cleanup on provider unmount", () => {
  it("aborts the in-flight run's AbortSignal when AppStateProvider unmounts", async () => {
    let capturedSignal: AbortSignal | undefined;
    runBackendChat.mockImplementation(
      (_sessionId: string, _message: string, _handlers: unknown, signal: AbortSignal) => {
        capturedSignal = signal;
        return new Promise(() => {}); // never resolves — simulates a still-in-flight turn
      },
    );

    const { unmount } = render(
      <AppStateProvider>
        <Harness />
      </AppStateProvider>,
    );

    await act(async () => {
      latest.setDraftText("hello");
    });
    act(() => {
      void latest.sendMessage();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);

    unmount();

    expect(capturedSignal!.aborted).toBe(true);
  });
});
