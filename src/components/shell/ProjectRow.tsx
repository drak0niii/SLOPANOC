import { useEffect, useMemo, useState } from "react";
import { Archive, ChevronRight, MoreHorizontal } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { cn } from "../../lib/cn";
import type { Chat, Project, ScheduledTask } from "../../types";
import { ScrollingText } from "../ui/ScrollingText";
import { SidebarChatRow } from "./SidebarChatRow";
import { SidebarTaskRow } from "./SidebarTaskRow";
import { ProjectRowMenu } from "./ProjectRowMenu";
import { DeleteProjectDialog } from "./DeleteProjectDialog";
import { sortChatsForDisplay } from "../../lib/chatSort";

interface ProjectRowProps {
  project: Project;
  chats: Chat[];
  /** This project's scheduled tasks. They live here rather than in the global
   * Tasks section, matching how the project's chats are grouped. */
  tasks: ScheduledTask[];
  expanded: boolean;
  onToggleExpand: () => void;
  isActiveWorkspace: boolean;
  activeChatId: string | null;
  onSelectChat: (chatId: string) => void;
}

export function ProjectRow({
  project,
  chats,
  tasks,
  expanded,
  onToggleExpand,
  isActiveWorkspace,
  activeChatId,
  onSelectChat,
}: ProjectRowProps) {
  const { enterProject, openProjectSettings, toggleProjectPinned, deleteProject } = useAppState();
  const sortedChats = useMemo(() => sortChatsForDisplay(chats), [chats]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  // Keep the chat list mounted for the duration of the collapse transition
  // (so it animates closed) and remove it from the DOM once finished, so
  // collapsed rows are never left keyboard-focusable.
  const [mounted, setMounted] = useState(expanded);
  useEffect(() => {
    if (expanded) setMounted(true);
  }, [expanded]);

  function handleEditDetails() {
    // Project settings only renders once the project's own home page is
    // mounted, so make sure we're actually looking at it first.
    enterProject(project.id);
    openProjectSettings("name");
  }

  return (
    <div>
      <div
        className={cn(
          "group flex items-center rounded-lg pr-1 text-base transition-colors duration-150",
          isActiveWorkspace || menuOpen
            ? "bg-surface-hover/50 text-primary"
            : "text-secondary hover:bg-surface-hover hover:text-primary",
        )}
      >
        {/* At rest this shows the project icon; hovering reveals the
            expand/collapse chevron in its place, so the row reads calmly
            when idle but still affords the toggle it always had. */}
        <button
          type="button"
          onClick={onToggleExpand}
          aria-label={expanded ? `Collapse ${project.name}` : `Expand ${project.name}`}
          aria-expanded={expanded}
          className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-tertiary transition-colors duration-150 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <Archive className="h-3.5 w-3.5 transition-opacity duration-100 group-hover:opacity-0" />
          <ChevronRight
            className={cn(
              "absolute h-3.5 w-3.5 opacity-0 transition-all duration-150 ease-premium group-hover:opacity-100",
              expanded && "rotate-90",
            )}
          />
        </button>

        <button
          type="button"
          onClick={() => enterProject(project.id)}
          className="min-w-0 flex-1 py-1.5 text-left focus-visible:outline-none"
        >
          <ScrollingText>{project.name}</ScrollingText>
        </button>

        <ProjectRowMenu
          project={project}
          open={menuOpen}
          onOpenChange={setMenuOpen}
          onTogglePin={() => toggleProjectPinned(project.id)}
          onEditDetails={handleEditDetails}
          onRequestDelete={() => setConfirmDeleteOpen(true)}
          trigger={
            <button
              type="button"
              aria-label="Project options"
              className={cn(
                "ml-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md opacity-0 transition-opacity duration-100",
                "hover:bg-surface-hover hover:text-primary",
                "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
                "group-hover:opacity-100",
                // Ternary, not an appended override — see SidebarChatRow.
                menuOpen ? "bg-surface-hover text-primary opacity-100" : "text-tertiary",
              )}
            >
              <MoreHorizontal className="h-3.5 w-3.5" />
            </button>
          }
        />
      </div>

      <DeleteProjectDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        projectName={project.name}
        onConfirm={() => deleteProject(project.id)}
      />

      <div
        className={cn(
          "grid transition-[grid-template-rows] duration-200 ease-premium",
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
        onTransitionEnd={(event) => {
          // Ignore bubbled transitionend events from descendants (e.g. a chat
          // row's hover color transition) — only react to this wrapper's own
          // grid-template-rows transition finishing.
          if (event.target !== event.currentTarget) return;
          if (!expanded) setMounted(false);
        }}
      >
        <div className="overflow-hidden">
          {mounted && (
            <div className="ml-3.5 mt-0.5 flex flex-col gap-0.5 border-l border-subtle/40 pl-2">
              {tasks.length === 0 && sortedChats.length === 0 && (
                <p className="px-2.5 py-1.5 text-sm text-tertiary">Nothing here yet</p>
              )}
              {tasks.map((task) => (
                <SidebarTaskRow key={task.id} task={task} />
              ))}
              {sortedChats.map((chat) => (
                <SidebarChatRow
                  key={chat.id}
                  chat={chat}
                  active={activeChatId === chat.id}
                  onSelect={() => onSelectChat(chat.id)}
                  showPinIndicator
                  showChatIcon
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
