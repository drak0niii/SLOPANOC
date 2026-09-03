import { describe, expect, it } from "vitest";
import { getSelectionCollapsedLabel } from "./selectionPresentation";
import type { SelectionCardView } from "./selectionCard";

describe("getSelectionCollapsedLabel", () => {
  it("uses the real selected label for a resolved selection, never a hardcoded example name", () => {
    const view: SelectionCardView = { kind: "resolved", selectedLabel: "Project Falcon Room Test" };
    expect(getSelectionCollapsedLabel(view)).toBe("Teams chat selected: Project Falcon Room Test");
  });

  it("has a fixed, generic label for a skipped selection", () => {
    const view: SelectionCardView = { kind: "skipped" };
    expect(getSelectionCollapsedLabel(view)).toBe("No Teams chat selected");
  });

  it("never embeds a checkmark/glyph character — the status icon already conveys it, avoiding a duplicate visual signal", () => {
    const resolved: SelectionCardView = { kind: "resolved", selectedLabel: "Project Falcon Room Test" };
    const skipped: SelectionCardView = { kind: "skipped" };
    for (const glyph of ["✓", "×", "✗", "✔"]) {
      expect(getSelectionCollapsedLabel(resolved)).not.toContain(glyph);
      expect(getSelectionCollapsedLabel(skipped)).not.toContain(glyph);
      expect(getSelectionCollapsedLabel({ kind: "stale" })).not.toContain(glyph);
      expect(getSelectionCollapsedLabel({ kind: "failed" })).not.toContain(glyph);
    }
  });

  it.each([
    ["stale", "Teams chat selection no longer active"],
    ["failed", "Teams chat selection failed"],
    ["choosing", "Selecting Teams chat…"],
    ["skipping", "Skipping…"],
    ["pending", "Choose a Teams chat"],
  ] as const)("maps view.kind %s to %s", (kind, expected) => {
    expect(getSelectionCollapsedLabel({ kind })).toBe(expected);
  });
});
