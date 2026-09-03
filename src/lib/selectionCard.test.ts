import { describe, expect, it } from "vitest";
import { classifySelectionFailure, deriveSelectionCardView } from "./selectionCard";
import { ApiError } from "../api/client";
import type { SelectionCardState } from "../types";

describe("deriveSelectionCardView — local card state takes priority", () => {
  it.each([
    ["choosing", "choosing"],
    ["skipping", "skipping"],
  ] as const)("maps card.phase %s to view.kind %s regardless of isCurrent", (phase, expectedKind) => {
    const card: SelectionCardState = { selectionId: "sel1", phase };
    expect(deriveSelectionCardView(true, card).kind).toBe(expectedKind);
    expect(deriveSelectionCardView(false, card).kind).toBe(expectedKind);
  });

  it("maps resolved with its selectedLabel", () => {
    const card: SelectionCardState = { selectionId: "sel1", phase: "resolved", selectedLabel: "Project Falcon Room Test" };
    const view = deriveSelectionCardView(false, card);
    expect(view.kind).toBe("resolved");
    expect(view.selectedLabel).toBe("Project Falcon Room Test");
  });

  it("maps skipped with no label needed", () => {
    const card: SelectionCardState = { selectionId: "sel1", phase: "skipped" };
    expect(deriveSelectionCardView(false, card).kind).toBe("skipped");
  });

  it("maps failed with its message", () => {
    const card: SelectionCardState = { selectionId: "sel1", phase: "failed", message: "exact backend message" };
    const view = deriveSelectionCardView(true, card);
    expect(view.kind).toBe("failed");
    expect(view.message).toBe("exact backend message");
  });
});

describe("deriveSelectionCardView — no local card state", () => {
  it("is pending when isCurrent is true", () => {
    expect(deriveSelectionCardView(true, undefined).kind).toBe("pending");
    expect(deriveSelectionCardView(true, null).kind).toBe("pending");
  });

  it("is stale when isCurrent is false — the conversation moved past it without resolving it", () => {
    expect(deriveSelectionCardView(false, undefined).kind).toBe("stale");
    expect(deriveSelectionCardView(false, null).kind).toBe("stale");
  });
});

describe("classifySelectionFailure", () => {
  it("uses the real ApiError message when present", () => {
    const error = new ApiError("This selection is no longer the active one for this session.", 409, {
      errorCode: "action_failure",
      reason: "selection_id_mismatch",
    });
    expect(classifySelectionFailure(error).message).toBe(
      "This selection is no longer the active one for this session.",
    );
  });

  it("falls back to a generic message for a non-ApiError failure", () => {
    expect(classifySelectionFailure(new TypeError("network down")).message).toBe(
      "Something went wrong. Please try again.",
    );
  });

  it("falls back to a generic message for an ApiError with an empty message", () => {
    const error = new ApiError("", 500);
    expect(classifySelectionFailure(error).message).toBe("Something went wrong. Please try again.");
  });
});
