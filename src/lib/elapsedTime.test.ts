import { describe, expect, it } from "vitest";
import { elapsedSecondsSince, formatElapsedTime } from "./elapsedTime";
import approvalCardSource from "../components/conversation/ApprovalCard.tsx?raw";

describe("formatElapsedTime", () => {
  it("formats under 60 seconds as plain seconds", () => {
    expect(formatElapsedTime(0)).toBe("0s");
    expect(formatElapsedTime(3)).toBe("3s");
    expect(formatElapsedTime(47)).toBe("47s");
    expect(formatElapsedTime(59)).toBe("59s");
  });

  it("formats at/above 60 seconds as minutes and zero-padded seconds", () => {
    expect(formatElapsedTime(60)).toBe("1m 00s");
    expect(formatElapsedTime(64)).toBe("1m 04s");
    expect(formatElapsedTime(151)).toBe("2m 31s");
  });

  it("never produces milliseconds and floors fractional input", () => {
    expect(formatElapsedTime(3.9)).toBe("3s");
  });

  it("never goes negative", () => {
    expect(formatElapsedTime(-5)).toBe("0s");
  });
});

describe("elapsedSecondsSince", () => {
  it("computes whole seconds elapsed, floored", () => {
    expect(elapsedSecondsSince(1000, 4500)).toBe(3);
  });

  it("never returns negative for a startedAt in the future (clock skew safety)", () => {
    expect(elapsedSecondsSince(5000, 1000)).toBe(0);
  });
});

describe("elapsed-time counter — unrelated to approval/proposal expiry", () => {
  it("ApprovalCard never references the elapsed-time module — this is a separate, unrelated concept", () => {
    // Structural guarantee: proves the elapsed-run counter and the
    // server-authoritative approval-expiry system remain two completely
    // separate mechanisms, per the locked "no visible approval timer"
    // requirement — this counter must never be wired into ApprovalCard.
    expect(approvalCardSource).not.toContain("elapsedTime");
    expect(approvalCardSource).not.toContain("formatElapsedTime");
    expect(approvalCardSource).not.toContain("runStartedAt");
  });
});
