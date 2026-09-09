import { describe, expect, it } from "vitest";
import type { KnowledgeSourceReferenceDTO } from "../api/types";
import {
  formatEvidenceTimestamp,
  formatKnowledgeSourceGroupLabel,
  formatKnowledgeSourceLabel,
  formatShortIsoDate,
  formatSourcePeriod,
  groupKnowledgeSourceReferences,
} from "./sourceReference";

function makeKnowledgeSource(overrides: Partial<KnowledgeSourceReferenceDTO> = {}): KnowledgeSourceReferenceDTO {
  return {
    source_id: "ks1",
    source_type: "knowledge",
    label: "Governed knowledge",
    knowledge_id: "aurora-relay-verification",
    version_label: "v1",
    section_id: "aurora-relay-verification:v1:s0",
    title: "Aurora Relay Verification Procedure",
    document_type: "technical_instruction",
    source_system: "manual_e2e_fixture",
    evidence_source_id: "doc-1",
    source_display_name: "Aurora Relay Governed Test Procedure",
    section_heading: "Verification",
    source_locator: "test-fixture:verification",
    content: "Confirm the checksum is 7319 and the status is GREEN.",
    ...overrides,
  };
}

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

describe("groupKnowledgeSourceReferences (POST-A5 refinement, Track B, final corrective pass)", () => {
  it("(A) same knowledge_id, same version, same source identity, different sections -> ONE group with both sections", () => {
    const verification = makeKnowledgeSource({
      section_id: "aurora:v1:s0",
      section_heading: "Verification",
      evidence_source_id: "doc-1",
    });
    const escalation = makeKnowledgeSource({
      section_id: "aurora:v1:s1",
      section_heading: "Escalation",
      evidence_source_id: "doc-1",
    });

    const groups = groupKnowledgeSourceReferences([verification, escalation]);

    expect(groups).toHaveLength(1);
    expect(groups[0].sections).toHaveLength(2);
    expect(groups[0].sections.map((s) => s.section_id)).toEqual(["aurora:v1:s0", "aurora:v1:s1"]);
  });

  it("(B) same knowledge_id, same version, DIFFERENT source identity -> TWO groups", () => {
    const fromFileOne = makeKnowledgeSource({ section_id: "aurora:v1:s0", evidence_source_id: "doc-1" });
    const fromFileTwo = makeKnowledgeSource({ section_id: "aurora:v1:s0", evidence_source_id: "doc-2" });

    const groups = groupKnowledgeSourceReferences([fromFileOne, fromFileTwo]);

    expect(groups).toHaveLength(2);
    expect(groups[0].evidenceSourceId).toBe("doc-1");
    expect(groups[1].evidenceSourceId).toBe("doc-2");
  });

  it("(C) same title, same version, different source identity -> TWO groups, never grouped by title", () => {
    const a = makeKnowledgeSource({
      knowledge_id: "doc-a",
      section_id: "doc-a:v1:s0",
      title: "Shared Title",
      evidence_source_id: "source-a",
    });
    const b = makeKnowledgeSource({
      knowledge_id: "doc-b",
      section_id: "doc-b:v1:s0",
      title: "Shared Title",
      evidence_source_id: "source-b",
    });

    const groups = groupKnowledgeSourceReferences([a, b]);

    expect(groups).toHaveLength(2);
    expect(groups[0].knowledgeId).toBe("doc-a");
    expect(groups[1].knowledgeId).toBe("doc-b");
  });

  it("(D) same source identity, different version -> TWO groups", () => {
    const v1 = makeKnowledgeSource({ version_label: "v1", section_id: "aurora:v1:s0", evidence_source_id: "doc-1" });
    const v2 = makeKnowledgeSource({ version_label: "v2", section_id: "aurora:v2:s0", evidence_source_id: "doc-1" });

    const groups = groupKnowledgeSourceReferences([v1, v2]);

    expect(groups).toHaveLength(2);
    expect(groups[0].versionLabel).toBe("v1");
    expect(groups[1].versionLabel).toBe("v2");
  });

  it("(E) same source/version, duplicate exact section_id -> deduped to one section, not a separate group", () => {
    const verification = makeKnowledgeSource({ section_id: "aurora:v1:s0" });
    const duplicate = makeKnowledgeSource({ section_id: "aurora:v1:s0", source_id: "different-synthetic-id" });

    const groups = groupKnowledgeSourceReferences([verification, duplicate]);

    expect(groups).toHaveLength(1);
    expect(groups[0].sections).toHaveLength(1);
  });

  it("(F) same source/version, two distinct section_ids with the SAME heading text -> both survive", () => {
    const first = makeKnowledgeSource({ section_id: "aurora:v1:s0", section_heading: "Verification" });
    const second = makeKnowledgeSource({ section_id: "aurora:v1:s1", section_heading: "Verification" });

    const groups = groupKnowledgeSourceReferences([first, second]);

    expect(groups).toHaveLength(1);
    expect(groups[0].sections).toHaveLength(2);
    expect(groups[0].sections.map((s) => s.section_id)).toEqual(["aurora:v1:s0", "aurora:v1:s1"]);
  });

  it("different source AND different version -> two separate groups", () => {
    const a = makeKnowledgeSource({ knowledge_id: "doc-a", version_label: "v1", section_id: "doc-a:v1:s0" });
    const b = makeKnowledgeSource({ knowledge_id: "doc-b", version_label: "v2", section_id: "doc-b:v2:s0" });

    const groups = groupKnowledgeSourceReferences([a, b]);

    expect(groups).toHaveLength(2);
  });

  it("(K) a single-section governed source still renders as one clean group", () => {
    const groups = groupKnowledgeSourceReferences([makeKnowledgeSource()]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sections).toHaveLength(1);
  });

  it("(L) an empty/undefined input produces no groups", () => {
    expect(groupKnowledgeSourceReferences([])).toEqual([]);
    expect(groupKnowledgeSourceReferences(undefined)).toEqual([]);
  });

  it("preserves first-seen group order and first-seen section order", () => {
    const docBFirst = makeKnowledgeSource({ knowledge_id: "doc-b", section_id: "doc-b:v1:s0" });
    const docASecond = makeKnowledgeSource({ knowledge_id: "doc-a", section_id: "doc-a:v1:s0" });
    const docAThirdSection = makeKnowledgeSource({ knowledge_id: "doc-a", section_id: "doc-a:v1:s1" });

    const groups = groupKnowledgeSourceReferences([docBFirst, docASecond, docAThirdSection]);

    expect(groups.map((g) => g.knowledgeId)).toEqual(["doc-b", "doc-a"]);
    expect(groups[1].sections.map((s) => s.section_id)).toEqual(["doc-a:v1:s0", "doc-a:v1:s1"]);
  });
});

describe("formatKnowledgeSourceGroupLabel (POST-A5 refinement, Track B)", () => {
  it("renders '<title> · <version_label>' — version is always visible on the chip", () => {
    const groups = groupKnowledgeSourceReferences([makeKnowledgeSource()]);
    expect(formatKnowledgeSourceGroupLabel(groups[0])).toBe("Aurora Relay Verification Procedure · v1");
  });

  it("falls back to source_display_name when title is blank", () => {
    const groups = groupKnowledgeSourceReferences([makeKnowledgeSource({ title: "" })]);
    expect(formatKnowledgeSourceGroupLabel(groups[0])).toBe("Aurora Relay Governed Test Procedure · v1");
  });

  it("falls back to the generic 'Governed knowledge · <version>' when no title/display name is usable", () => {
    const groups = groupKnowledgeSourceReferences([makeKnowledgeSource({ title: "", source_display_name: null })]);
    expect(formatKnowledgeSourceGroupLabel(groups[0])).toBe("Governed knowledge · v1");
  });
});
