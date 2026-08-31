import { useRef, useState, type ReactNode } from "react";
import { Plus } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { IconButton } from "../ui/IconButton";
import { Tooltip } from "../ui/Tooltip";
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogRoot,
  DialogTitle,
  DialogTrigger,
} from "../ui/Dialog";

const DEFAULT_TRIGGER = (
  <IconButton label="New project" size="sm">
    <Plus className="h-3.5 w-3.5" />
  </IconButton>
);

/** Matches the counter shown under the field. Enforced by `maxLength` too, so
 * the count can never run past it. */
const DESCRIPTION_LIMIT = 1000;

export function CreateProjectDialog({ trigger = DEFAULT_TRIGGER }: { trigger?: ReactNode }) {
  const { createProject } = useAppState();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setName("");
      setDescription("");
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    createProject(trimmedName, description.trim() || undefined);
    handleOpenChange(false);
  }

  return (
    <DialogRoot open={open} onOpenChange={handleOpenChange}>
      <Tooltip label="New project">
        <DialogTrigger asChild>{trigger}</DialogTrigger>
      </Tooltip>

      <DialogContent
        className="w-full max-w-lg p-6"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          inputRef.current?.focus();
        }}
      >
        {/* pr-10 keeps the heading clear of the close button, which the
            Dialog positions absolutely in the same corner. */}
        <DialogTitle className="pr-10 text-lg">Create a new project</DialogTitle>
        <DialogDescription>
          Projects keep related chats, files, and instructions together, so context carries across
          your work.
        </DialogDescription>

        <form onSubmit={handleSubmit} className="mt-5">
          <label htmlFor="project-name" className="text-sm font-medium text-primary">
            Title
          </label>
          <input
            id="project-name"
            ref={inputRef}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Automated Operations"
            className="mt-1.5 w-full rounded-xl border border-subtle bg-surface px-3 py-2.5 text-base text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
          />

          <div className="mt-4">
            <label htmlFor="project-description" className="text-sm font-medium text-primary">
              Description <span className="font-normal text-tertiary">(optional)</span>
            </label>
            <textarea
              id="project-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={DESCRIPTION_LIMIT}
              rows={3}
              placeholder="What is this project for?"
              className="mt-1.5 w-full resize-none rounded-xl border border-subtle bg-surface px-3 py-2.5 text-base leading-relaxed text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
            />
            <p className="mt-1 text-right text-xs text-tertiary">
              {description.length} / {DESCRIPTION_LIMIT}
            </p>
          </div>

          <div className="mt-6 flex justify-end gap-2">
            <DialogClose asChild>
              <button
                type="button"
                className="inline-flex h-9 items-center rounded-lg px-3.5 text-base font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              >
                Cancel
              </button>
            </DialogClose>
            <button
              type="submit"
              disabled={!name.trim()}
              className="inline-flex h-9 items-center rounded-lg bg-accent px-4 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
            >
              Create
            </button>
          </div>
        </form>
      </DialogContent>
    </DialogRoot>
  );
}
