import { describe, expect, it } from "vitest";
import { getActionOperationPresentation } from "./actionOperationLabels";

describe("getActionOperationPresentation — deterministic operation -> button-label mapping", () => {
  it("teams.createChat maps to the exact label 'Create'", () => {
    expect(getActionOperationPresentation("teams.createChat")).toEqual({ primaryButtonLabel: "Create" });
  });

  it("teams.sendMessage maps to the exact label 'Send'", () => {
    expect(getActionOperationPresentation("teams.sendMessage")).toEqual({ primaryButtonLabel: "Send" });
  });

  it("an unrecognized future operation falls back to a safe generic label, never a guess", () => {
    expect(getActionOperationPresentation("sharepoint.uploadFile")).toEqual({ primaryButtonLabel: "Approve" });
  });

  it("the fallback is never empty, and never any of the forbidden generic phrasings", () => {
    const { primaryButtonLabel } = getActionOperationPresentation("some.unknown.operation");
    expect(primaryButtonLabel).toBeTruthy();
    expect(primaryButtonLabel).not.toMatch(/approve & /i);
    expect(primaryButtonLabel).not.toMatch(/confirm action/i);
  });
});
