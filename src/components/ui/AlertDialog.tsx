import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { cn } from "../../lib/cn";

export const AlertDialogRoot = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;

export function AlertDialogContent({
  children,
  className,
  ...props
}: AlertDialogPrimitive.AlertDialogContentProps) {
  return (
    <AlertDialogPrimitive.Portal>
      <AlertDialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 bg-inverse/30 backdrop-blur-[2px]",
          "data-[state=open]:anim-overlay-in data-[state=closed]:anim-overlay-out",
        )}
      />
      <AlertDialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-[22rem] -translate-x-1/2 -translate-y-1/2",
          "rounded-2xl border border-subtle/50 bg-surface-raised/95 p-5 shadow-xl shadow-black/20 backdrop-blur-xl",
          "data-[state=open]:anim-effort-in data-[state=closed]:anim-effort-out",
          "focus-visible:outline-none",
          className,
        )}
        {...props}
      >
        {children}
      </AlertDialogPrimitive.Content>
    </AlertDialogPrimitive.Portal>
  );
}

export function AlertDialogTitle({ children }: { children: React.ReactNode }) {
  return (
    <AlertDialogPrimitive.Title className="text-base font-medium text-primary">
      {children}
    </AlertDialogPrimitive.Title>
  );
}

export function AlertDialogDescription({ children }: { children: React.ReactNode }) {
  return (
    <AlertDialogPrimitive.Description className="mt-1.5 text-base leading-relaxed text-tertiary">
      {children}
    </AlertDialogPrimitive.Description>
  );
}

export const AlertDialogCancel = AlertDialogPrimitive.Cancel;
export const AlertDialogAction = AlertDialogPrimitive.Action;
