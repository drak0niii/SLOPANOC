import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight } from "./icons";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export const MenuRoot = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;

export function MenuContent({
  children,
  align = "start",
  className,
  ...props
}: DropdownMenu.DropdownMenuContentProps) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        align={align}
        sideOffset={8}
        collisionPadding={12}
        className={cn(
          "z-50 min-w-[14rem] rounded-2xl border border-subtle/50 bg-surface-raised/95 p-1.5 shadow-xl shadow-black/20 backdrop-blur-xl",
          "data-[state=open]:anim-pop-in data-[state=closed]:anim-pop-out",
          className,
        )}
        {...props}
      >
        {children}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  );
}

export function MenuItem({
  children,
  icon,
  className,
  ...props
}: DropdownMenu.DropdownMenuItemProps & { icon?: ReactNode }) {
  return (
    <DropdownMenu.Item
      className={cn(
        "flex cursor-pointer select-none items-center gap-3 rounded-xl px-3 py-2.5 text-base text-primary outline-none transition-colors duration-100",
        "hover:bg-surface-hover focus:bg-surface-hover",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-40",
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </DropdownMenu.Item>
  );
}

export function MenuCheckboxItem({
  children,
  checked,
  className,
  ...props
}: DropdownMenu.DropdownMenuCheckboxItemProps) {
  return (
    <DropdownMenu.CheckboxItem
      checked={checked}
      className={cn(
        "flex cursor-pointer select-none items-center justify-between gap-2.5 rounded-xl px-3 py-2.5 text-base text-primary outline-none transition-colors duration-100",
        "hover:bg-surface-hover focus:bg-surface-hover",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-40",
        className,
      )}
      {...props}
    >
      <span className="truncate">{children}</span>
      <DropdownMenu.ItemIndicator>
        <Check className="h-4 w-4 text-accent" />
      </DropdownMenu.ItemIndicator>
    </DropdownMenu.CheckboxItem>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <DropdownMenu.Label className="px-2.5 pb-1 pt-2 text-sm font-medium uppercase tracking-wide text-tertiary">
      {children}
    </DropdownMenu.Label>
  );
}

export function MenuSeparator() {
  return <DropdownMenu.Separator className="my-1.5 h-px bg-subtle" />;
}

export const MenuSub = DropdownMenu.Sub;

export function MenuSubTrigger({
  children,
  icon,
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <DropdownMenu.SubTrigger
      className={cn(
        "group flex cursor-pointer select-none items-center gap-3 rounded-xl px-3 py-2.5 text-base text-primary outline-none transition-colors duration-100",
        "hover:bg-surface-hover focus:bg-surface-hover data-[state=open]:bg-surface-hover",
        className,
      )}
    >
      {icon}
      <span className="min-w-0 flex-1">{children}</span>
      <ChevronRight className="h-4 w-4 shrink-0 text-tertiary transition-transform duration-150 group-data-[state=open]:rotate-90" />
    </DropdownMenu.SubTrigger>
  );
}

export function MenuSubContent({ children }: { children: ReactNode }) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.SubContent
        sideOffset={4}
        collisionPadding={12}
        className="z-50 min-w-[15rem] rounded-2xl border border-subtle/50 bg-surface-raised/95 p-1.5 shadow-xl shadow-black/20 backdrop-blur-xl data-[state=open]:anim-pop-in data-[state=closed]:anim-pop-out"
      >
        {children}
      </DropdownMenu.SubContent>
    </DropdownMenu.Portal>
  );
}
