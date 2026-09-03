import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CurrentActivity } from "./CurrentActivity";

const NOW = new Date("2026-01-01T00:00:00.000Z").getTime();

describe("CurrentActivity", () => {
  it("renders the label verbatim inside an atomic status live region", () => {
    render(
      <CurrentActivity label="Retrieving recent messages from the selected Teams conversation" startedAt={NOW} />,
    );
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "true");
    expect(region).toHaveTextContent("Retrieving recent messages from the selected Teams conversation");
  });

  it("wraps rather than truncating a long label (no truncate class, no ellipsis)", () => {
    const longLabel =
      "Reviewing the retrieved evidence across a very long conversation thread that could plausibly overflow a narrow container";
    render(<CurrentActivity label={longLabel} startedAt={NOW} />);
    const region = screen.getByRole("status");
    expect(region.className).not.toMatch(/\btruncate\b/);
    expect(region.className).toMatch(/\bbreak-words\b/);
    expect(region).toHaveTextContent(longLabel);
  });

  it("never renders a hardcoded 'Thinking' placeholder string", () => {
    render(<CurrentActivity label="Processing your request" startedAt={NOW} />);
    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();
  });
});

describe("CurrentActivity — elapsed-time counter (interaction-capability extension)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows 0s immediately when the run just started", () => {
    render(<CurrentActivity label="Processing your request" startedAt={NOW} />);
    expect(screen.getByText("· 0s")).toBeInTheDocument();
  });

  it("increments roughly once per second while mounted", () => {
    render(<CurrentActivity label="Processing your request" startedAt={NOW} />);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText("· 3s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("· 4s")).toBeInTheDocument();
  });

  it("continues counting from the SAME startedAt across a label change — never resets", () => {
    const { rerender } = render(<CurrentActivity label="Thinking…" startedAt={NOW} />);
    act(() => {
      vi.advanceTimersByTime(11000);
    });
    expect(screen.getByText("· 11s")).toBeInTheDocument();

    rerender(<CurrentActivity label="Reviewing the selected Teams conversation" startedAt={NOW} />);
    // Still 11s -- the label changed, the elapsed clock did not reset.
    expect(screen.getByText("· 11s")).toBeInTheDocument();
    expect(screen.getByText("Reviewing the selected Teams conversation")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.getByText("· 17s")).toBeInTheDocument();
  });

  it("formats under 60 seconds as plain seconds", () => {
    render(<CurrentActivity label="x" startedAt={NOW} />);
    act(() => {
      vi.advanceTimersByTime(47000);
    });
    expect(screen.getByText("· 47s")).toBeInTheDocument();
  });

  it("formats at/above 60 seconds as minutes and zero-padded seconds", () => {
    render(<CurrentActivity label="x" startedAt={NOW} />);
    act(() => {
      vi.advanceTimersByTime(64000); // 1m 04s
    });
    expect(screen.getByText("· 1m 04s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(87000); // total 151s -> 2m 31s
    });
    expect(screen.getByText("· 2m 31s")).toBeInTheDocument();
  });

  it("the elapsed suffix is aria-hidden -- excluded from the accessible tree, never separately announced", () => {
    render(<CurrentActivity label="Processing your request" startedAt={NOW} />);
    const elapsedNode = screen.getByText("· 0s");
    expect(elapsedNode).toHaveAttribute("aria-hidden", "true");
  });

  it("stops ticking (clears its interval) on unmount, never leaking a timer", () => {
    const clearSpy = vi.spyOn(window, "clearInterval");
    const { unmount } = render(<CurrentActivity label="Processing your request" startedAt={NOW} />);
    unmount();
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});
