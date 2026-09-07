import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// This file targets ComposerPlusMenu's own POST-5.1 B3 file-picker wiring
// (the hidden <input type="file">, always present in the DOM regardless of
// the Radix dropdown's open/closed state) — not the Radix menu's own
// open/close behavior, which is a library concern exercised elsewhere.
const mockAppState = {
  activeConnectorIds: [] as string[],
  toggleConnector: vi.fn(),
  queueImageFiles: vi.fn(),
  addDraftSource: vi.fn(),
  activeProject: null as null,
  connectorList: [] as unknown[],
  state: { workspaceScope: { type: "general" as const }, draft: { sources: [] as unknown[] } },
  activeChat: null as { demoRun?: unknown } | null,
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { ComposerPlusMenu } from "./ComposerPlusMenu";
import { TooltipProvider } from "../ui/Tooltip";

function renderMenu() {
  return render(
    <TooltipProvider>
      <ComposerPlusMenu />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  mockAppState.queueImageFiles.mockClear();
  mockAppState.state = { workspaceScope: { type: "general" }, draft: { sources: [] } };
  mockAppState.activeChat = null;
});

describe("ComposerPlusMenu — POST-5.1 B3 real image picker wiring", () => {
  it("only accepts the three supported image MIME types on the hidden file input", () => {
    const { container } = renderMenu();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.accept).toBe("image/png,image/jpeg,image/webp");
    expect(input.multiple).toBe(true);
  });

  it("routes selected files through queueImageFiles, the same central path paste/drag-drop use", () => {
    const { container } = renderMenu();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(10)], "photo.png", { type: "image/png" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(mockAppState.queueImageFiles).toHaveBeenCalledExactlyOnceWith([file]);
  });

  it("clears the input's value after selection, so re-picking the same file fires a change event again", () => {
    const { container } = renderMenu();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array(10)], "photo.png", { type: "image/png" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(input.value).toBe("");
  });

  it("does nothing when the change event carries no files", () => {
    const { container } = renderMenu();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [] } });

    expect(mockAppState.queueImageFiles).not.toHaveBeenCalled();
  });

  it("renders the 'Add images' trigger for the file-attach affordance", () => {
    renderMenu();
    expect(screen.getByRole("button", { name: "Add files, chat rooms, or connectors" })).toBeInTheDocument();
  });
});
