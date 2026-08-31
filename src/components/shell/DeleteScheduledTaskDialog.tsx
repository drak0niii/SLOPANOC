import {
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogRoot,
  AlertDialogTitle,
} from "../ui/AlertDialog";

export function DeleteScheduledTaskDialog({
  open,
  onOpenChange,
  taskName,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  taskName: string;
  onConfirm: () => void;
}) {
  return (
    <AlertDialogRoot open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogTitle>Delete scheduled task?</AlertDialogTitle>
        <AlertDialogDescription>
          "{taskName}" will stop running and its schedule will be removed. Past runs stay in your
          chat list. This can't be undone.
        </AlertDialogDescription>
        <div className="mt-5 flex justify-end gap-2">
          <AlertDialogCancel asChild>
            <button
              type="button"
              className="inline-flex h-8 items-center rounded-lg px-3 text-base font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Cancel
            </button>
          </AlertDialogCancel>
          <AlertDialogAction asChild>
            <button
              type="button"
              onClick={onConfirm}
              className="inline-flex h-8 items-center rounded-lg bg-danger px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
            >
              Delete
            </button>
          </AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialogRoot>
  );
}
