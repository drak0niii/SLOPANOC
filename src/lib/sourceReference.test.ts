import { describe, expect, it } from "vitest";
import {
  formatEvidenceTimestamp,
  formatKnowledgeSourceLabel,
  formatShortIsoDate,
  formatSourcePeriod,
} from "./sourceReference";

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

describe("formatKnowledgeSourceLabel (POST-5.1 B7 corrective pass)", () => {
  it("combines title and section heading when both are present", () => {
    const label = formatKnowledgeSourceLabel({
      title: "Aurora Relay Verification Procedure",
      section_heading: "Verification",
      source_display_name: "Aurora Relay Governed Test Procedure",
    });
    expect(label).toBe("Aurora Relay Verification Procedure · Verification");
  });

  it("produces a different label for a different section of the SAME document — two references stay distinguishable", () => {
    const verification = formatKnowledgeSourceLabel({
      title: "Aurora Relay Verification Procedure",
      section_heading: "Verification",
      source_display_name: null,
    });
    const escalation = formatKnowledgeSourceLabel({
      title: "Aurora Relay Verification Procedure",
      section_heading: "Escalation",
      source_display_name: null,
    });
    expect(verification).not.toBe(escalation);
    expect(verification).toBe("Aurora Relay Verification Procedure · Verification");
    expect(escalation).toBe("Aurora Relay Verification Procedure · Escalation");
  });

  it("falls back to the title alone when section_heading is null", () => {
    expect(
      formatKnowledgeSourceLabel({
        title: "Aurora Relay Verification Procedure",
        section_heading: null,
        source_display_name: "Aurora Relay Governed Test Procedure",
      }),
    ).toBe("Aurora Relay Verification Procedure");
  });

  it("falls back to the title alone when section_heading is blank/whitespace", () => {
    expect(
      formatKnowledgeSourceLabel({
        title: "Aurora Relay Verification Procedure",
        section_heading: "   ",
        source_display_name: null,
      }),
    ).toBe("Aurora Relay Verification Procedure");
  });

  it("falls back to source_display_name when title is blank (defensive only)", () => {
    expect(
      formatKnowledgeSourceLabel({
        title: "",
        section_heading: "Verification",
        source_display_name: "Aurora Relay Governed Test Procedure",
      }),
    ).toBe("Aurora Relay Governed Test Procedure");
  });

  it("falls back to the generic 'Governed knowledge' when no metadata is usable at all", () => {
    expect(
      formatKnowledgeSourceLabel({
        title: "",
        section_heading: null,
        source_display_name: null,
      }),
    ).toBe("Governed knowledge");
  });
});
