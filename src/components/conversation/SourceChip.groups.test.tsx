import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceChip } from "./SourceChip";
import type { KnowledgeSourceReferenceDTO } from "../../api/types";
import { groupKnowledgeSourceReferences } from "../../lib/sourceReference";

/** POST-A5 refinement (Track B) — component-level coverage for the new
 * `kind: "knowledge-group"` variant. `SourceChip.test.tsx`'s existing
 * `kind: "knowledge"` (single-DTO) tests are left completely untouched —
 * this file only exercises the NEW consolidated presentation, built on
 * top of the same trusted DTOs via `groupKnowledgeSourceReferences`. */

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

describe("SourceChip — knowledge-group (POST-A5 refinement, Track B)", () => {
  it("(A/G) renders ONE chip for two sections of the same source+version, with two matched-section chips and two evidence blocks", () => {
    const verification = makeKnowledgeSource({ section_id: "aurora:v1:s0", section_heading: "Verification" });
    const escalation = makeKnowledgeSource({
      section_id: "aurora:v1:s1",
      section_heading: "Escalation",
      content: "If verification fails, collect the observed values and escalate to the platform owner.",
    });
    const [group] = groupKnowledgeSourceReferences([verification, escalation]);

    render(<SourceChip kind="knowledge-group" group={group} />);

    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v1/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button"));

    // Matched sections (blue chips).
    const verificationChips = screen.getAllByText("Verification");
    const escalationChips = screen.getAllByText("Escalation");
    expect(verificationChips.length).toBeGreaterThan(0);
    expect(escalationChips.length).toBeGreaterThan(0);

    // Supporting evidence, per section, exact content, no cross-contamination.
    expect(screen.getByText('"Confirm the checksum is 7319 and the status is GREEN."')).toBeInTheDocument();
    expect(
      screen.getByText('"If verification fails, collect the observed values and escalate to the platform owner."'),
    ).toBeInTheDocument();
  });

  it("(B) an exact-duplicate section reference is shown once, not twice, in matched sections and evidence", () => {
    const verification = makeKnowledgeSource({ section_id: "aurora:v1:s0", section_heading: "Verification" });
    const duplicate = makeKnowledgeSource({
      section_id: "aurora:v1:s0",
      section_heading: "Verification",
      source_id: "different-synthetic-id",
    });
    const [group] = groupKnowledgeSourceReferences([verification, duplicate]);

    render(<SourceChip kind="knowledge-group" group={group} />);
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getAllByText('"Confirm the checksum is 7319 and the status is GREEN."')).toHaveLength(1);
  });

  it("(C) same source, different version -> two SEPARATE chips/drawers, never combined", () => {
    const v1 = makeKnowledgeSource({ version_label: "v1", section_id: "aurora:v1:s0" });
    const v2 = makeKnowledgeSource({ version_label: "v2", section_id: "aurora:v2:s0" });
    const groups = groupKnowledgeSourceReferences([v1, v2]);

    render(
      <>
        {groups.map((g) => (
          <SourceChip key={g.groupKey} kind="knowledge-group" group={g} />
        ))}
      </>,
    );

    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v1/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Source · Aurora Relay Verification Procedure · v2/ }),
    ).toBeInTheDocument();
  });

  it("(B) same knowledge_id+version but DIFFERENT source identity -> two separate chips, never merged", () => {
    const fromFileOne = makeKnowledgeSource({ section_id: "aurora:v1:s0", evidence_source_id: "doc-1" });
    const fromFileTwo = makeKnowledgeSource({ section_id: "aurora:v1:s0", evidence_source_id: "doc-2" });
    const groups = groupKnowledgeSourceReferences([fromFileOne, fromFileTwo]);

    render(
      <>
        {groups.map((g) => (
          <SourceChip key={g.groupKey} kind="knowledge-group" group={g} />
        ))}
      </>,
    );

    expect(groups).toHaveLength(2);
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("(K) a single-section governed source still renders cleanly as one chip with one matched section", () => {
    const [group] = groupKnowledgeSourceReferences([makeKnowledgeSource()]);
    render(<SourceChip kind="knowledge-group" group={group} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getAllByText("Verification").length).toBeGreaterThan(0);
    expect(screen.getByText('"Confirm the checksum is 7319 and the status is GREEN."')).toBeInTheDocument();
  });

  it("version is visible in both the message-level chip and the drawer header", () => {
    const [group] = groupKnowledgeSourceReferences([makeKnowledgeSource({ version_label: "v3" })]);
    render(<SourceChip kind="knowledge-group" group={group} />);

    expect(screen.getByRole("button", { name: /· v3/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("v3")).toBeInTheDocument();
  });

  it("matched-section chips use the design system's blue/info tone token", () => {
    const [group] = groupKnowledgeSourceReferences([makeKnowledgeSource()]);
    render(<SourceChip kind="knowledge-group" group={group} />);
    fireEvent.click(screen.getByRole("button"));

    const matchedSectionChip = screen.getByText("Verification", { selector: "span" });
    expect(matchedSectionChip.className).toMatch(/text-info/);
    expect(matchedSectionChip.className).toMatch(/bg-info/);
  });

  it("never renders source_uri or a gs:// URI", () => {
    const [group] = groupKnowledgeSourceReferences([makeKnowledgeSource()]);
    render(<SourceChip kind="knowledge-group" group={group} />);
    fireEvent.click(screen.getByRole("button"));
    const drawerText = document.body.textContent ?? "";
    expect(drawerText).not.toContain("gs://");
    expect(drawerText.toLowerCase()).not.toContain("source_uri");
  });
});
