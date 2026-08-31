import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "./icons";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export const DialogRoot = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({
  children,
  className,
  showClose = true,
  ...props
}: DialogPrimitive.DialogContentProps & { showClose?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 bg-inverse/30 backdrop-blur-[2px]",
          "data-[state=open]:anim-overlay-in data-[state=closed]:anim-overlay-out",
        )}
      />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
          "rounded-2xl border border-subtle/50 bg-surface-raised/95 shadow-xl shadow-black/20 backdrop-blur-xl",
          "data-[state=open]:anim-effort-in data-[state=closed]:anim-effort-out",
          "focus-visible:outline-none",
          className,
        )}
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close
            aria-label="Close"
            className="absolute right-4 top-4 rounded-md p-1 text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Title className={cn("text-base font-medium text-primary", className)}>
      {children}
    </DialogPrimitive.Title>
  );
}

export function DialogDescription({ children }: { children: React.ReactNode }) {
  return (
    <DialogPrimitive.Description className="mt-1.5 text-base leading-relaxed text-tertiary">
      {children}
    </DialogPrimitive.Description>
  );
}

/**
 * Standard panel header for content rendered inside DialogContent.
 * Reserves space on the right so an optional panel-level action (e.g. "Add files",
 * "Create skill") never overlaps the Dialog's absolutely-positioned close button.
 */
export function DialogPanelHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 pr-12">
      <div>
        <h3 className="text-base font-medium text-primary">{title}</h3>
        {description && <p className="mt-1 text-sm leading-relaxed text-tertiary">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export const DialogClose = DialogPrimitive.Close;
