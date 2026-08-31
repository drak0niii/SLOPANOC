import { Eye, EyeOff, FolderInput, FolderMinus, Pencil, Pin, PinOff, Trash2 } from "../ui/icons";
import type { ReactNode } from "react";
import { useAppState } from "../../state/AppState";
import type { Chat } from "../../types";
import {
  MenuContent,
  MenuItem,
  MenuRoot,
  MenuSeparator,
  MenuSub,
  MenuSubContent,
  MenuSubTrigger,
  MenuTrigger,
} from "../ui/Menu";

interface ChatRowMenuProps {
  chat: Chat;
  trigger: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRename: () => void;
  onTogglePin: () => void;
  onRequestDelete: () => void;
}

export function ChatRowMenu({
  chat,
  trigger,
  open,
  onOpenChange,
  onRename,
  onTogglePin,
  onRequestDelete,
}: ChatRowMenuProps) {
  const { projects, moveChatToProject, toggleUnread } = useAppState();
  const otherProjects = projects.filter((project) => project.id !== chat.projectId);
  const showGeneralOption = chat.projectId !== null;

  return (
    <MenuRoot open={open} onOpenChange={onOpenChange}>
      <MenuTrigger asChild>{trigger}</MenuTrigger>
      <MenuContent align="start" className="w-52">
        <MenuItem
          icon={
            chat.pinned ? (
              <PinOff className="h-4 w-4 text-secondary" />
            ) : (
              <Pin className="h-4 w-4 text-secondary" />
            )
          }
          onSelect={onTogglePin}
        >
          {chat.pinned ? "Unpin" : "Pin"}
        </MenuItem>
        <MenuItem
          icon={
            chat.unread ? (
              <Eye className="h-4 w-4 text-secondary" />
            ) : (
              <EyeOff className="h-4 w-4 text-secondary" />
            )
          }
          onSelect={() => toggleUnread(chat.id)}
        >
          {chat.unread ? "Mark as read" : "Mark as unread"}
        </MenuItem>
        <MenuItem icon={<Pencil className="h-4 w-4 text-secondary" />} onSelect={onRename}>
          Rename
        </MenuItem>
        <MenuSub>
          <MenuSubTrigger icon={<FolderInput className="h-4 w-4 text-secondary" />}>
            Change project
          </MenuSubTrigger>
          <MenuSubContent>
            {projects.length === 0 ? (
              <div className="max-w-56 px-3 py-2.5">
                <p className="text-base font-medium text-primary">No projects yet</p>
                <p className="mt-1 text-sm leading-relaxed text-tertiary">
                  Projects will appear here once you create one.
                </p>
              </div>
            ) : otherProjects.length === 0 ? (
              <div className="max-w-56 px-3 py-2.5">
                <p className="text-sm leading-relaxed text-tertiary">
                  No other projects to move this chat to yet.
                </p>
              </div>
            ) : (
              otherProjects.map((project) => (
                <MenuItem key={project.id} onSelect={() => moveChatToProject(chat.id, project.id)}>
                  <span className="truncate">{project.name}</span>
                </MenuItem>
              ))
            )}
          </MenuSubContent>
        </MenuSub>
        {showGeneralOption && (
          <MenuItem
            icon={<FolderMinus className="h-4 w-4 text-secondary" />}
            onSelect={() => moveChatToProject(chat.id, null)}
          >
            Remove from project
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          icon={<Trash2 className="h-4 w-4 text-danger" />}
          onSelect={onRequestDelete}
          className="hover:bg-danger/10"
        >
          <span className="text-danger">Delete</span>
        </MenuItem>
      </MenuContent>
    </MenuRoot>
  );
}
