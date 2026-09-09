import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Chat } from "../../types";

/**
 * POST-B7 corrective pass — closure evidence for the previously-deferred
 * "click/select, rename, and pin remain unaffected" claim. This file did
 * not exist before this pass (grep confirmed zero existing test coverage
 * of `SidebarChatRow`/`togglePin` anywhere in the repo) — these are
 * therefore new, not duplicates, and are scoped to exactly the three
 * interactions this corrective pass's own `ScrollingText` change could
 * plausibly have affected (it did not touch `SidebarChatRow.tsx` at
 * all), using a genuinely OVERFLOWING title so the hover/scroll change
 * is exercised in the same render as click/rename/pin.
 */
const mockAppState: {
  renameChat: ReturnType<typeof vi.fn>;
  deleteChat: ReturnType<typeof vi.fn>;
  togglePin: ReturnType<typeof vi.fn>;
  projects: unknown[];
  moveChatToProject: ReturnType<typeof vi.fn>;
  toggleUnread: ReturnType<typeof vi.fn>;
} = {
  renameChat: vi.fn(),
  deleteChat: vi.fn(),
  togglePin: vi.fn(),
  projects: [],
  moveChatToProject: vi.fn(),
  toggleUnread: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { SidebarChatRow } from "./SidebarChatRow";

function makeChat(overrides: Partial<Chat> = {}): Chat {
  return {
    id: "chat-1",
    title: "A genuinely long overflowing chat title that needs horizontal scrolling to read in full",
    projectId: null,
    pinned: false,
    createdAt: 0,
    messageIds: [],
    activeSkillId: null,
    connectorIds: [],
    selectedModelId: "default",
    thinkingEffort: "instant",
    ...overrides,
  };
}

/** Radix's `DropdownMenu.Trigger` (v2.x) opens on `pointerdown`, not
 * `click` — jsdom's `fireEvent.click` alone never dispatches a preceding
 * pointer event, so the menu never opens with a bare click in tests. */
function openChatOptionsMenu() {
  const trigger = screen.getByRole("button", { name: "Chat options" });
  fireEvent.pointerDown(trigger, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(trigger, { button: 0, pointerId: 1 });
  fireEvent.click(trigger);
}

beforeEach(() => {
  mockAppState.renameChat.mockClear();
  mockAppState.deleteChat.mockClear();
  mockAppState.togglePin.mockClear();
  mockAppState.moveChatToProject.mockClear();
  mockAppState.toggleUnread.mockClear();
});

describe("SidebarChatRow — click/select, rename, pin (POST-B7 closure evidence)", () => {
  it("clicking the row calls onSelect, with a genuinely overflowing title present", () => {
    const onSelect = vi.fn();
    render(<SidebarChatRow chat={makeChat()} active={false} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: makeChat().title }));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("opening the row menu and choosing Rename enters edit mode, and committing calls renameChat", async () => {
    render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} />);

    openChatOptionsMenu();
    const renameItem = await screen.findByText("Rename");
    fireEvent.click(renameItem);

    const input = await screen.findByLabelText("Chat title");
    fireEvent.change(input, { target: { value: "Renamed chat title" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(mockAppState.renameChat).toHaveBeenCalledExactlyOnceWith("chat-1", "Renamed chat title");
  });

  it("opening the row menu and choosing Pin calls togglePin with the chat id", async () => {
    render(<SidebarChatRow chat={makeChat({ pinned: false })} active={false} onSelect={vi.fn()} />);

    openChatOptionsMenu();
    const pinItem = await screen.findByText("Pin");
    fireEvent.click(pinItem);

    expect(mockAppState.togglePin).toHaveBeenCalledExactlyOnceWith("chat-1");
  });

  it("an already-pinned chat's menu offers Unpin, still calling togglePin with the chat id", async () => {
    render(<SidebarChatRow chat={makeChat({ pinned: true })} active={false} onSelect={vi.fn()} />);

    openChatOptionsMenu();
    const unpinItem = await screen.findByText("Unpin");
    fireEvent.click(unpinItem);

    expect(mockAppState.togglePin).toHaveBeenCalledExactlyOnceWith("chat-1");
  });

  it("a short, non-overflowing title still supports click/select and rename identically", async () => {
    const onSelect = vi.fn();
    render(<SidebarChatRow chat={makeChat({ title: "Short" })} active={false} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "Short" }));
    expect(onSelect).toHaveBeenCalledOnce();

    openChatOptionsMenu();
    fireEvent.click(await screen.findByText("Rename"));
    expect(await screen.findByLabelText("Chat title")).toHaveValue("Short");
  });
});

describe("SidebarChatRow — typography, spacing, and selected-row truncation (Sidebar Chats micro-correction)", () => {
  it("(C) the row uses the reduced (text-sm) typography step, not the larger text-base", () => {
    const { container } = render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} />);
    const row = container.firstElementChild as HTMLElement;
    expect(row.className).toMatch(/\btext-sm\b/);
    expect(row.className).not.toMatch(/\btext-base\b/);
  });

  it("(D) the dot/title row uses an explicit, larger flex gap than before (gap-2, not gap-1.5)", () => {
    render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} showChatIcon />);
    const titleButton = screen.getByRole("button", { name: makeChat().title });
    expect(titleButton.className).toMatch(/\bgap-2\b/);
    expect(titleButton.className).not.toMatch(/\bgap-1\.5\b/);
  });

  it("(E) the title wrapper is structurally truncatable: min-w-0 flex-1 inside an overflow-hidden, single-line ScrollingText", () => {
    render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} />);
    const titleButton = screen.getByRole("button", { name: makeChat().title });
    expect(titleButton.className).toMatch(/\bmin-w-0\b/);
    expect(titleButton.className).toMatch(/\bflex-1\b/);
    // ScrollingText's own outer span (min-w-0 overflow-hidden whitespace-nowrap).
    const scrollingOuter = titleButton.querySelector("span")!;
    expect(scrollingOuter.className).toMatch(/\boverflow-hidden\b/);
    expect(scrollingOuter.className).toMatch(/\bwhitespace-nowrap\b/);
  });

  it("(F) the chat-options menu trigger reserves fixed end-of-row space (shrink-0), identically for selected and non-selected rows", () => {
    const nonSelected = render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} />);
    const selected = render(<SidebarChatRow chat={makeChat()} active={true} onSelect={vi.fn()} />);
    const nonSelectedTrigger = within(nonSelected.container).getByRole("button", { name: "Chat options" });
    const selectedTrigger = within(selected.container).getByRole("button", { name: "Chat options" });
    expect(nonSelectedTrigger.className).toMatch(/\bshrink-0\b/);
    expect(selectedTrigger.className).toMatch(/\bshrink-0\b/);
    // The reserved space is present in the DOM/layout regardless of
    // hover — opacity-0 hides it visually without removing its box.
    expect(nonSelectedTrigger.className).toMatch(/\bopacity-0\b/);
    expect(selectedTrigger.className).toMatch(/\bopacity-0\b/);
  });

  it("(G) the selected chat's title never starts the delayed hover-scroll animation, even after a long hover", () => {
    const { container } = render(<SidebarChatRow chat={makeChat()} active={true} onSelect={vi.fn()} />);
    const titleButton = screen.getByRole("button", { name: makeChat().title });
    const scrollingOuter = titleButton.querySelector("span") as HTMLElement;
    const inner = scrollingOuter.firstElementChild as HTMLElement;
    Object.defineProperty(scrollingOuter, "clientWidth", { value: 100, configurable: true });
    Object.defineProperty(inner, "scrollWidth", { value: 300, configurable: true });

    vi.useFakeTimers();
    try {
      fireEvent.mouseEnter(scrollingOuter);
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(container.querySelector(".anim-scroll-text")).toBeNull();
      // UI MICRO-POLISH: no visible "..." ellipsis and no fade — a plain,
      // stable (non-scrolling) hard clip.
      expect(inner.className).not.toMatch(/\btext-ellipsis\b/);
      expect(scrollingOuter.className).not.toMatch(/\bfade-edge-right\b/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("(H) a non-selected row's existing delayed hover-scroll behavior is completely unaffected", () => {
    render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} />);
    const titleButton = screen.getByRole("button", { name: makeChat().title });
    const scrollingOuter = titleButton.querySelector("span") as HTMLElement;
    const inner = scrollingOuter.firstElementChild as HTMLElement;
    Object.defineProperty(scrollingOuter, "clientWidth", { value: 100, configurable: true });
    Object.defineProperty(inner, "scrollWidth", { value: 300, configurable: true });

    vi.useFakeTimers();
    try {
      fireEvent.mouseEnter(scrollingOuter);
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(scrollingOuter.firstElementChild?.className).toContain("anim-scroll-text");
    } finally {
      vi.useRealTimers();
    }
  });

  it("(I) selected and non-selected rows start the title at the identical horizontal position (same dot column, same gap, same padding)", () => {
    const nonSelected = render(<SidebarChatRow chat={makeChat()} active={false} onSelect={vi.fn()} showChatIcon />);
    const selected = render(<SidebarChatRow chat={makeChat()} active={true} onSelect={vi.fn()} showChatIcon />);
    const nonSelectedRow = nonSelected.container.firstElementChild as HTMLElement;
    const selectedRow = selected.container.firstElementChild as HTMLElement;
    const nonSelectedButton = within(nonSelected.container).getByRole("button", { name: makeChat().title });
    const selectedButton = within(selected.container).getByRole("button", { name: makeChat().title });

    // Only background/text-color classes may legitimately differ between
    // selected and non-selected — padding, gap, and typography must not.
    for (const token of ["pl-2.5", "pr-1", "py-2", "text-sm"]) {
      expect(nonSelectedRow.className).toContain(token);
      expect(selectedRow.className).toContain(token);
    }
    for (const token of ["gap-2", "min-w-0", "flex-1"]) {
      expect(nonSelectedButton.className).toContain(token);
      expect(selectedButton.className).toContain(token);
    }
  });
});
