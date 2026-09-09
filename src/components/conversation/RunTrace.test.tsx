import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunTrace } from "./RunTrace";
import type { RunTraceStep } from "../../types";

const NOW = new Date("2026-01-01T00:00:00.000Z").getTime();

function steps(overrides: Partial<RunTraceStep>[] = []): RunTraceStep[] {
  const defaults: RunTraceStep[] = [
    { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" },
    {
      stepId: "s2",
      category: "evidence",
      label: "Reviewed 42 retrieved messages",
      status: "completed",
      safeMetadata: { message_count: 42 },
    },
    { stepId: "s3", category: "response", label: "Generated the response", status: "completed" },
  ];
  if (overrides.length === 0) return defaults;
  return overrides.map((o, i) => ({ ...defaults[i % defaults.length], ...o }));
}

/** UI PRESENTATION CORRECTION (post-Phase-2, "Runtime Activity
 * Truthfulness — UI Presentation Correction"): while a run is live,
 * RunTrace is deliberately a SINGLE LINE — no chevron, no expandable
 * history, no semantic icon, regardless of how much activity/trace-step
 * history exists behind the scenes (that history is still accumulated
 * internally — see AppState.tsx — just never exposed here while live).
 * Only `mode: "completed"` ever shows a disclosure. */
describe("RunTrace — live mode", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders exactly one activity line: the current backend label verbatim, plus the ticking timer", () => {
    render(
      <RunTrace mode="live" label="Reviewing the selected Teams conversation" startedAt={NOW} steps={[]} expanded={false} onToggle={() => {}} />,
    );
    expect(screen.getByText("Reviewing the selected Teams conversation")).toBeInTheDocument();
    expect(screen.getByText("· 0s")).toBeInTheDocument();
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("never shows a disclosure chevron, even with a rich permanent trace-step history behind the scenes", () => {
    render(
      <RunTrace mode="live" label="Reviewing evidence" startedAt={NOW} steps={steps()} expanded={false} onToggle={() => {}} />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("never shows a disclosure chevron even when the record's own `expanded` flag is true", () => {
    render(
      <RunTrace mode="live" label="Reviewing evidence" startedAt={NOW} steps={steps()} expanded={true} onToggle={() => {}} />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("never renders a semantic activity icon (no svg beside the label)", () => {
    render(<RunTrace mode="live" label="Searching governed knowledge" startedAt={NOW} steps={[]} expanded={false} onToggle={() => {}} />);
    const region = screen.getByRole("status");
    expect(region.querySelector("svg")).not.toBeInTheDocument();
  });

  it("never renders any prior/permanent activity underneath the current line", () => {
    render(<RunTrace mode="live" label="Validating supporting evidence" startedAt={NOW} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByText("Used the selected Teams conversation")).not.toBeInTheDocument();
    expect(screen.queryByText("Reviewed 42 retrieved messages")).not.toBeInTheDocument();
    expect(screen.queryByText("Generated the response")).not.toBeInTheDocument();
  });

  it("replaces the visible line (never appends) when the label changes — still exactly one line", () => {
    const { rerender } = render(
      <RunTrace mode="live" label="Searching governed knowledge" startedAt={NOW} steps={[]} expanded={false} onToggle={() => {}} />,
    );
    expect(screen.getByText("Searching governed knowledge")).toBeInTheDocument();

    rerender(<RunTrace mode="live" label="Reviewing retrieved knowledge" startedAt={NOW} steps={[]} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByText("Searching governed knowledge")).not.toBeInTheDocument();
    expect(screen.getByText("Reviewing retrieved knowledge")).toBeInTheDocument();
    expect(screen.getAllByRole("status")).toHaveLength(1);

    rerender(<RunTrace mode="live" label="Validating supporting evidence" startedAt={NOW} steps={[]} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByText("Reviewing retrieved knowledge")).not.toBeInTheDocument();
    expect(screen.getByText("Validating supporting evidence")).toBeInTheDocument();
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("keeps ticking regardless of the record's own expanded flag — expansion state is irrelevant to live mode now", () => {
    render(<RunTrace mode="live" label="x" startedAt={NOW} steps={[]} expanded={true} onToggle={() => {}} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("· 5s")).toBeInTheDocument();
  });
});

describe("RunTrace — completed mode", () => {
  it("renders 'Worked for Xs' on a successful run", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.getByText("Worked for 34s")).toBeInTheDocument();
  });

  it("renders 'Stopped after Xs' on a failed run, never a success header", () => {
    render(<RunTrace mode="completed" outcome="error" durationSeconds={12} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.getByText("Stopped after 12s")).toBeInTheDocument();
    expect(screen.queryByText(/worked for/i)).not.toBeInTheDocument();
  });

  it("never renders a chain-of-thought-style header", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByText(/thought for/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/reasoned for/i)).not.toBeInTheDocument();
  });

  it("expands to show every step verbatim, in order, when clicked", () => {
    let expanded = false;
    const onToggle = vi.fn(() => {
      expanded = true;
    });
    const { rerender } = render(
      <RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={expanded} onToggle={onToggle} />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledOnce();

    rerender(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={true} onToggle={onToggle} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("Used the selected Teams conversation"),
      expect.stringContaining("Reviewed 42 retrieved messages"),
      expect.stringContaining("Generated the response"),
    ]);
  });

  it("renders the SAME neutral icon for every completed step, including the final one — never a distinct green checkmark", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={true} onToggle={() => {}} />);
    const items = screen.getAllByRole("listitem");
    const iconClasses = items.map((item) => item.querySelector("svg")?.getAttribute("class"));
    // All three default steps are "completed" -- every icon must render
    // identically, first through last.
    expect(iconClasses[0]).toBe(iconClasses[1]);
    expect(iconClasses[1]).toBe(iconClasses[2]);
    // None of them may be the green success tone.
    for (const cls of iconClasses) {
      expect(cls).not.toMatch(/text-success/);
    }
  });

  it("renders a user-stopped run's terminal step with 'Stopped after Xs', distinct from a genuine error, but the same neutral step icons", () => {
    render(
      <RunTrace mode="completed" outcome="stopped" durationSeconds={12} steps={steps()} expanded={false} onToggle={() => {}} />,
    );
    expect(screen.getByText("Stopped after 12s")).toBeInTheDocument();
  });

  it("renders a warning step distinctly from a completed step", () => {
    const withWarning = steps([
      {},
      { status: "warning", label: "No matching Teams content was found" },
      {},
    ]);
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={5} steps={withWarning} expanded={true} onToggle={() => {}} />);
    expect(screen.getByText("No matching Teams content was found")).toBeInTheDocument();
  });

  it("renders a failed step distinctly, e.g. for an error run's terminal step", () => {
    const withFailure = [
      { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed" as const },
      { stepId: "s2", category: "teams", label: "Could not complete the Teams request", status: "failed" as const },
      { stepId: "s3", category: "response", label: "Request could not be completed", status: "failed" as const },
    ];
    render(<RunTrace mode="completed" outcome="error" durationSeconds={5} steps={withFailure} expanded={true} onToggle={() => {}} />);
    expect(screen.getByText("Request could not be completed")).toBeInTheDocument();
  });

  it("never renders safe_metadata raw — only the pre-rendered label", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={true} onToggle={() => {}} />);
    expect(screen.queryByText(/message_count/)).not.toBeInTheDocument();
  });

  it("has no expand affordance when there are zero steps", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={1} steps={[]} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("RunTrace — accessibility", () => {
  it("uses aria-expanded/aria-controls on the disclosure button", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={true} onToggle={() => {}} />);
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAttribute("aria-controls");
    const controlsId = button.getAttribute("aria-controls");
    expect(document.getElementById(controlsId!)).toBeInTheDocument();
  });

  it("does not wrap the steps list in an aria-live region (no per-step announcement flooding)", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={34} steps={steps()} expanded={true} onToggle={() => {}} />);
    const list = screen.getAllByRole("list").find((el) => el.tagName === "UL" && el.id);
    expect(list).not.toHaveAttribute("aria-live");
  });
});

describe("RunTrace — expandable-trace visibility threshold (>2 meaningful steps)", () => {
  it("hides the expand affordance entirely with exactly 1 step — the header still renders", () => {
    const oneStep = steps().slice(0, 1);
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={4} steps={oneStep} expanded={false} onToggle={() => {}} />);
    expect(screen.getByText("Worked for 4s")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("hides the expand affordance entirely with exactly 2 steps — the header still renders", () => {
    const twoSteps = steps().slice(0, 2);
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={4} steps={twoSteps} expanded={false} onToggle={() => {}} />);
    expect(screen.getByText("Worked for 4s")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("never shows the step list for <=2 steps even when the record's own expanded flag is true", () => {
    const twoSteps = steps().slice(0, 2);
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={4} steps={twoSteps} expanded={true} onToggle={() => {}} />);
    expect(screen.queryByText("Used the selected Teams conversation")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows the expand affordance once there are more than 2 steps (3)", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={4} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("live mode never shows the expand affordance, regardless of step count — the >2 threshold applies to completed mode only", () => {
    render(<RunTrace mode="live" label="Working" startedAt={NOW} steps={steps()} expanded={false} onToggle={() => {}} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();

    const expandedFlagSet = render(
      <RunTrace mode="live" label="Working" startedAt={NOW} steps={steps()} expanded={true} onToggle={() => {}} />,
    );
    expect(expandedFlagSet.queryByRole("button")).not.toBeInTheDocument();
    expect(expandedFlagSet.queryByRole("list")).not.toBeInTheDocument();
  });
});

describe("RunTrace — chevron placement (layout contract)", () => {
  it("the disclosure button hugs its content width rather than stretching full-width, so the chevron sits close to the header text", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={24} steps={steps()} expanded={false} onToggle={() => {}} />);
    const button = screen.getByRole("button");
    // A full-width button would push the chevron to the far edge via a
    // flex-1 spacer; this asserts no such spacer/full-width class remains
    // on the button itself.
    expect(button.className).not.toMatch(/\bw-full\b/);
  });

  it("the header text is not stretched with flex-1 in completed mode (no spacer pushing the chevron away)", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={24} steps={steps()} expanded={false} onToggle={() => {}} />);
    const headerText = screen.getByText("Worked for 24s");
    expect(headerText.className).not.toMatch(/\bflex-1\b/);
  });

  it("the chevron immediately follows the header text as the button's only two children", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={24} steps={steps()} expanded={false} onToggle={() => {}} />);
    const button = screen.getByRole("button");
    expect(button.children).toHaveLength(2);
    expect(button.children[0]).toHaveTextContent("Worked for 24s");
    expect(button.children[1].tagName.toLowerCase()).toBe("svg");
  });
});

describe("RunTrace — spacing below the trace, before whatever renders next (layout contract)", () => {
  it("when expanded, the LAST step row supplies the trailing gap itself (pb-2.5, same token used between every other step) rather than the root's own margin", () => {
    const { container } = render(
      <RunTrace mode="completed" outcome="ok" durationSeconds={20} steps={steps()} expanded={true} onToggle={() => {}} />,
    );
    const root = container.firstElementChild as HTMLElement;
    const list = screen.getByRole("list");
    const items = screen.getAllByRole("listitem");
    const lastItem = items[items.length - 1];

    expect(list.className).toMatch(/\bmt-1\.5\b/);
    // The last row is no longer specially zeroed — it carries the exact
    // same pb-2.5 every other row already uses for inter-step spacing
    // (instruction: never alter spacing BETWEEN individual steps; this
    // reuses that same, unmodified token for the trailing gap instead of
    // introducing a new value).
    expect(lastItem.className).toMatch(/\bpb-2\.5\b/);
    expect(lastItem.className).not.toMatch(/\blast:pb-0\b/);
    // The root element does NOT ALSO add its own bottom margin in this
    // case — exactly one mechanism supplies the trailing gap, never both
    // stacked into an oversized margin.
    expect(root.className).not.toMatch(/\bmb-2\.5\b/);
  });

  it("when collapsed (no visible step list), the root element's own mb-2.5 supplies an equivalent trailing gap", () => {
    const { container } = render(
      <RunTrace mode="completed" outcome="ok" durationSeconds={20} steps={steps()} expanded={false} onToggle={() => {}} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toMatch(/\bmb-2\.5\b/);
  });

  it("carries the equivalent trailing gap in live mode too, and for an error/'stopped' terminal state", () => {
    const live = render(<RunTrace mode="live" label="Working" startedAt={Date.now()} steps={[]} expanded={false} onToggle={() => {}} />);
    expect((live.container.firstElementChild as HTMLElement).className).toMatch(/\bmb-2\.5\b/);

    const stoppedCollapsed = render(
      <RunTrace mode="completed" outcome="stopped" durationSeconds={5} steps={steps()} expanded={false} onToggle={() => {}} />,
    );
    expect((stoppedCollapsed.container.firstElementChild as HTMLElement).className).toMatch(/\bmb-2\.5\b/);
  });

  it("reuses the pb-2.5 spacing token — the same distance already used between individual trace steps — for the trailing gap, never a larger arbitrary value", () => {
    const { container } = render(
      <RunTrace mode="completed" outcome="ok" durationSeconds={20} steps={steps()} expanded={true} onToggle={() => {}} />,
    );
    const items = screen.getAllByRole("listitem");
    // Every row (including the last) now shares the exact same pb-2.5 —
    // proving the trailing gap is literally the SAME token/value as the
    // gap between any two consecutive steps, not a new arbitrary margin.
    for (const item of items) {
      expect(item.className).toMatch(/\bpb-2\.5\b/);
    }
    const root = container.firstElementChild as HTMLElement;
    // And the root never introduces an unrelated large margin on top.
    expect(root.className).not.toMatch(/\bm[bty]-(3|4|5|6|8)\b/);
  });

  it("does not change spacing BETWEEN individual (non-last) trace steps", () => {
    render(<RunTrace mode="completed" outcome="ok" durationSeconds={20} steps={steps()} expanded={true} onToggle={() => {}} />);
    const items = screen.getAllByRole("listitem");
    // Every non-last row still carries exactly pb-2.5 -- unchanged.
    for (const item of items.slice(0, -1)) {
      expect(item.className).toContain("pb-2.5");
    }
  });
});
