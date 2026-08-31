import { useState } from "react";
import { Clock, MoreHorizontal, Pause, Play, Settings2, Trash2 } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { cn } from "../../lib/cn";
import type { ScheduledTask } from "../../types";
import { MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger } from "../ui/Menu";
import { ScrollingText } from "../ui/ScrollingText";
import { DeleteScheduledTaskDialog } from "./DeleteScheduledTaskDialog";

/**
 * A task and the chat it owns are one row. Clicking opens the conversation
 * where its runs land; the task's own settings live behind the menu, so the
 * common action (read my brief) is the cheap one.
 */
export function SidebarTaskRow({ task }: { task: ScheduledTask }) {
  const {
    state,
    viewScheduledTask,
    toggleScheduledTaskActive,
    deleteScheduledTask,
    runScheduledTask,
    selectChat,
  } = useAppState();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const chat = task.chatId ? state.chats[task.chatId] : undefined;
  const active =
    (chat && state.activeChatId === chat.id && state.mainView === "workspace") ||
    (state.mainView === "scheduledTaskDetail" && state.activeScheduledTaskId === task.id);

  return (
    <div
      className={cn(
        "group relative flex w-full items-center rounded-lg py-2 pl-2.5 pr-1 text-base transition-colors duration-150",
        active || menuOpen
          ? "bg-surface-hover/50 text-primary"
          : "text-secondary hover:bg-surface-hover hover:text-primary",
      )}
    >
      <button
        type="button"
        onClick={() => (chat ? selectChat(chat.id) : viewScheduledTask(task.id))}
        aria-current={active ? "true" : undefined}
        className="flex min-w-0 flex-1 items-center gap-1.5 text-left focus-visible:outline-none"
      >
        <Clock
          // Active vs paused is carried by contrast rather than hue, since
          // the system is monochrome.
          className={cn("h-3 w-3 shrink-0", task.active ? "text-primary" : "text-tertiary")}
          aria-hidden="true"
        />
        {chat?.unread && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
        )}
        <ScrollingText className={cn("flex-1", chat?.unread && "font-medium text-primary")}>
          {task.name}
        </ScrollingText>
      </button>

      <MenuRoot open={menuOpen} onOpenChange={setMenuOpen}>
        <MenuTrigger asChild>
          <button
            type="button"
            aria-label="Task options"
            className={cn(
              "ml-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md opacity-0 transition-opacity duration-100",
              "hover:bg-surface-hover hover:text-primary",
              "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
              "group-hover:opacity-100",
              menuOpen ? "bg-surface-hover text-primary opacity-100" : "text-tertiary",
            )}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </MenuTrigger>
        <MenuContent align="start" className="w-48">
          <MenuItem
            icon={<Play className="h-4 w-4 text-secondary" />}
            onSelect={() => runScheduledTask(task.id, { focus: false })}
          >
            Run now
          </MenuItem>
          <MenuItem
            icon={<Settings2 className="h-4 w-4 text-secondary" />}
            onSelect={() => viewScheduledTask(task.id)}
          >
            Task settings
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            icon={
              task.active ? (
                <Pause className="h-4 w-4 text-secondary" />
              ) : (
                <Play className="h-4 w-4 text-secondary" />
              )
            }
            onSelect={() => toggleScheduledTaskActive(task.id)}
          >
            {task.active ? "Pause" : "Resume"}
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            icon={<Trash2 className="h-4 w-4 text-danger" />}
            onSelect={() => setConfirmDeleteOpen(true)}
            className="hover:bg-danger/10"
          >
            <span className="text-danger">Delete</span>
          </MenuItem>
        </MenuContent>
      </MenuRoot>

      <DeleteScheduledTaskDialog
        open={confirmDeleteOpen}
        onOpenChange={setConfirmDeleteOpen}
        taskName={task.name}
        onConfirm={() => deleteScheduledTask(task.id)}
      />
    </div>
  );
}
