import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "../../lib/cn";

export const DrawerRoot = DialogPrimitive.Root;
export const DrawerTrigger = DialogPrimitive.Trigger;

export function DrawerContent({
  children,
  className,
  showClose = true,
  ...props
}: DialogPrimitive.DialogContentProps & { showClose?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 bg-inverse/20",
          "data-[state=open]:anim-overlay-in data-[state=closed]:anim-overlay-out",
        )}
      />
      <DialogPrimitive.Content
        className={cn(
          "fixed right-0 top-0 z-50 h-full w-full max-w-[380px]",
          "border-l border-subtle/50 bg-surface-raised/95 shadow-xl shadow-black/20 backdrop-blur-xl",
          "data-[state=open]:anim-drawer-in data-[state=closed]:anim-drawer-out",
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

export function DrawerTitle({ children }: { children: React.ReactNode }) {
  return (
    <DialogPrimitive.Title className="pr-8 text-sm font-medium leading-snug text-primary">
      {children}
    </DialogPrimitive.Title>
  );
}

export const DrawerClose = DialogPrimitive.Close;
