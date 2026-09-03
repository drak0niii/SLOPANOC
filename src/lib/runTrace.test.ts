import { describe, expect, it } from "vitest";
import { deriveRunTraceStepIcon, formatCompletedTraceHeader } from "./runTrace";
import type { RunTraceStep } from "../types";

function step(overrides: Partial<RunTraceStep> = {}): RunTraceStep {
  return { stepId: "s1", category: "teams", label: "Used the selected Teams conversation", status: "completed", ...overrides };
}

describe("formatCompletedTraceHeader", () => {
  it("uses a neutral 'Worked for' header on success, never a chain-of-thought phrase", () => {
    expect(formatCompletedTraceHeader("ok", 34)).toBe("Worked for 34s");
  });

  it("formats minutes the same way formatElapsedTime does", () => {
    expect(formatCompletedTraceHeader("ok", 64)).toBe("Worked for 1m 04s");
  });

  it("uses a distinct, non-success header on error", () => {
    expect(formatCompletedTraceHeader("error", 34)).toBe("Stopped after 34s");
  });

  it("uses the same 'Stopped after Xs' header for a user-initiated stop", () => {
    expect(formatCompletedTraceHeader("stopped", 12)).toBe("Stopped after 12s");
  });

  it("never produces a 'Thought for'/'Reasoned for' style label", () => {
    const header = formatCompletedTraceHeader("ok", 12);
    expect(header.toLowerCase()).not.toContain("thought");
    expect(header.toLowerCase()).not.toContain("reason");
  });
});

describe("deriveRunTraceStepIcon", () => {
  it("gives every completed step the same neutral clock icon, regardless of position", () => {
    expect(deriveRunTraceStepIcon(step({ status: "completed" }))).toBe("clock");
  });

  it("never gives the final completed step a distinct green success checkmark", () => {
    // Positional distinction was removed entirely -- there is no longer
    // an "isLastStep" concept in this function at all.
    expect(deriveRunTraceStepIcon.length).toBe(1);
  });

  it("gives a warning step the warning icon", () => {
    expect(deriveRunTraceStepIcon(step({ status: "warning" }))).toBe("warning");
  });

  it("gives a failed step the failure icon", () => {
    expect(deriveRunTraceStepIcon(step({ status: "failed" }))).toBe("failed");
  });
});
