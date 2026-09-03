import { describe, expect, it } from "vitest";
import { formatEvidenceTimestamp, formatShortIsoDate, formatSourcePeriod } from "./sourceReference";

describe("formatShortIsoDate", () => {
  it("formats an ISO timestamp as a short month/day", () => {
    expect(formatShortIsoDate("2026-08-26T09:00:00Z")).toMatch(/Aug 2[56]/);
  });

  it("never throws on a malformed value, falling back to the raw string", () => {
    expect(formatShortIsoDate("not a date")).toBe("not a date");
  });
});

describe("formatSourcePeriod", () => {
  it("formats a full range when both ends are known and differ", () => {
    const result = formatSourcePeriod("2026-08-26T09:00:00Z", "2026-09-01T09:00:00Z");
    expect(result).toContain("–");
  });

  it("collapses to a single date when both ends land on the same day", () => {
    const result = formatSourcePeriod("2026-08-26T09:00:00Z", "2026-08-26T18:00:00Z");
    expect(result).not.toContain("–");
  });

  it("uses whichever end is present when only one is known", () => {
    expect(formatSourcePeriod("2026-08-26T09:00:00Z", null)).not.toBeNull();
    expect(formatSourcePeriod(null, "2026-08-26T09:00:00Z")).not.toBeNull();
  });

  it("returns null when neither end is known", () => {
    expect(formatSourcePeriod(null, null)).toBeNull();
    expect(formatSourcePeriod(undefined, undefined)).toBeNull();
  });
});

describe("formatEvidenceTimestamp", () => {
  it("includes both a date and a time", () => {
    const result = formatEvidenceTimestamp("2026-08-26T09:00:00Z");
    expect(result).toMatch(/Aug 2[56]/);
    expect(result).toMatch(/\d{1,2}:\d{2}/);
  });

  it("never throws on a malformed value", () => {
    expect(formatEvidenceTimestamp("garbage")).toBe("garbage");
  });
});
