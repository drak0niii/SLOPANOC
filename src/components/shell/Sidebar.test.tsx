import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Sidebar Chats micro-correction — structural coverage for the
 * scrollbar-gutter fix. Deliberately minimal AppState mock (empty
 * chats/projects/tasks): the defect and its fix live entirely in the
 * scroll container's own className and the section headers' own
 * (state-independent) className, both of which render regardless of
 * list contents — no real SidebarChatRow/ProjectRow instance is needed
 * to prove either claim.
 */
const mockAppState: {
  state: {
    sidebarCollapsed: boolean;
    activeScheduledTaskId: string | null;
    scheduledTasks: Record<string, unknown>;
    mainView: string;
    workspaceScope: { type: "general" };
    savedChatsHydrationStatus: "loaded";
    settingsModal: { open: boolean; section: string };
  };
  chatList: unknown[];
  activeChat: null;
  projects: unknown[];
  newChat: ReturnType<typeof vi.fn>;
  selectChat: ReturnType<typeof vi.fn>;
  toggleSidebar: ReturnType<typeof vi.fn>;
  openSettings: ReturnType<typeof vi.fn>;
  closeSettings: ReturnType<typeof vi.fn>;
  openScheduledTasks: ReturnType<typeof vi.fn>;
  openAllProjects: ReturnType<typeof vi.fn>;
  startScheduledTaskSetup: ReturnType<typeof vi.fn>;
  retrySavedChats: ReturnType<typeof vi.fn>;
} = {
  state: {
    sidebarCollapsed: false,
    activeScheduledTaskId: null,
    scheduledTasks: {},
    mainView: "chat",
    workspaceScope: { type: "general" },
    savedChatsHydrationStatus: "loaded",
    settingsModal: { open: false, section: "usage" },
  },
  chatList: [],
  activeChat: null,
  projects: [],
  newChat: vi.fn(),
  selectChat: vi.fn(),
  toggleSidebar: vi.fn(),
  openSettings: vi.fn(),
  closeSettings: vi.fn(),
  openScheduledTasks: vi.fn(),
  openAllProjects: vi.fn(),
  startScheduledTaskSetup: vi.fn(),
  retrySavedChats: vi.fn(),
};

vi.mock("../../state/AppState", () => ({
  useAppState: () => mockAppState,
}));

import { Sidebar } from "./Sidebar";
import { TooltipProvider } from "../ui/Tooltip";
import { RouterProvider } from "../../lib/router";

beforeEach(() => {
  mockAppState.state.sidebarCollapsed = false;
});

function renderSidebar() {
  return render(
    <RouterProvider>
      <TooltipProvider>
        <Sidebar />
      </TooltipProvider>
    </RouterProvider>,
  );
}

describe("Sidebar — Chats scrollbar-gutter correction", () => {
  it("(A) the shared Projects/Scheduler/Chats scroll container permanently reserves scrollbar-gutter space", () => {
    const { container } = renderSidebar();
    const scrollContainer = container.querySelector(".overflow-y-auto") as HTMLElement;
    expect(scrollContainer).not.toBeNull();
    expect(scrollContainer.className).toContain("[scrollbar-gutter:stable]");
  });

  it("(B) the gutter reservation is present identically whether Chats is collapsed or expanded — never a state-dependent class", () => {
    const { container } = renderSidebar();
    const scrollContainer = container.querySelector(".overflow-y-auto") as HTMLElement;
    const beforeExpand = scrollContainer.className;

    fireEvent.click(screen.getByRole("button", { name: "Expand Chats" }));

    const afterExpand = (container.querySelector(".overflow-y-auto") as HTMLElement).className;
    expect(afterExpand).toBe(beforeExpand);
    expect(afterExpand).toContain("[scrollbar-gutter:stable]");
  });

  it("(B) the Chats section header row's own className never changes between collapsed and expanded — no compensating margin/spacing is added on toggle", () => {
    renderSidebar();
    const chatsToggle = screen.getByRole("button", { name: "Expand Chats" });
    const headerRow = chatsToggle.closest("div.group") as HTMLElement;
    const collapsedClassName = headerRow.className;

    fireEvent.click(chatsToggle);

    const expandedToggle = screen.getByRole("button", { name: "Collapse Chats" });
    const expandedHeaderRow = expandedToggle.closest("div.group") as HTMLElement;
    expect(expandedHeaderRow.className).toBe(collapsedClassName);
  });

  it("(J) clicking the Chats chevron still toggles aria-expanded and reveals/hides the list content — collapse/expand semantics unchanged", () => {
    renderSidebar();
    const chatsToggle = screen.getByRole("button", { name: "Expand Chats" });
    expect(chatsToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("No chats yet")).not.toBeInTheDocument();

    fireEvent.click(chatsToggle);

    const expandedToggle = screen.getByRole("button", { name: "Collapse Chats" });
    expect(expandedToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("No chats yet")).toBeInTheDocument();

    fireEvent.click(expandedToggle);
    expect(screen.getByRole("button", { name: "Expand Chats" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("No chats yet")).not.toBeInTheDocument();
  });

  it("Projects/Scheduler section headers sit in the SAME gutter-reserved scroll container as Chats", () => {
    const { container } = renderSidebar();
    const scrollContainer = container.querySelector(".overflow-y-auto") as HTMLElement;
    expect(within(scrollContainer).getByRole("button", { name: "Expand Projects" })).toBeInTheDocument();
    expect(within(scrollContainer).getByRole("button", { name: "Expand Scheduler" })).toBeInTheDocument();
    expect(within(scrollContainer).getByRole("button", { name: "Expand Chats" })).toBeInTheDocument();
  });

  it("does not render the scrollable sections at all while the sidebar itself is collapsed (unaffected by this fix)", () => {
    mockAppState.state.sidebarCollapsed = true;
    const { container } = renderSidebar();
    expect(container.querySelector(".overflow-y-auto")).toBeNull();
  });
});
