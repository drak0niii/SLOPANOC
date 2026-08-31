import { Pencil, Pin, PinOff, Trash2 } from "../ui/icons";
import type { ReactNode } from "react";
import type { Project } from "../../types";
import { MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger } from "../ui/Menu";

interface ProjectRowMenuProps {
  project: Project;
  trigger: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTogglePin: () => void;
  onEditDetails: () => void;
  onRequestDelete: () => void;
}

export function ProjectRowMenu({
  project,
  trigger,
  open,
  onOpenChange,
  onTogglePin,
  onEditDetails,
  onRequestDelete,
}: ProjectRowMenuProps) {
  return (
    <MenuRoot open={open} onOpenChange={onOpenChange}>
      <MenuTrigger asChild>{trigger}</MenuTrigger>
      <MenuContent align="start" className="w-52">
        <MenuItem
          icon={
            project.pinned ? (
              <PinOff className="h-4 w-4 text-secondary" />
            ) : (
              <Pin className="h-4 w-4 text-secondary" />
            )
          }
          onSelect={onTogglePin}
        >
          {project.pinned ? "Unpin" : "Pin"}
        </MenuItem>
        <MenuItem icon={<Pencil className="h-4 w-4 text-secondary" />} onSelect={onEditDetails}>
          Edit details
        </MenuItem>
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
