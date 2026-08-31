import { useEffect, useRef, useState } from "react";
import { Plus, Trash2 } from "../../ui/icons";
import { useAppState } from "../../../state/AppState";
import type { Skill } from "../../../types";
import {
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogRoot,
  AlertDialogTitle,
} from "../../ui/AlertDialog";
import { DialogContent, DialogPanelHeader, DialogRoot, DialogTitle } from "../../ui/Dialog";

const secondaryButtonClass =
  "inline-flex h-8 items-center rounded-lg px-3 text-base font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50";

const fieldClass =
  "mt-1.5 w-full rounded-lg border border-subtle bg-surface px-3 py-2 text-base text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none";

function SkillEditorDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: Skill | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { createSkill, updateSkill } = useAppState();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName(skill?.name ?? "");
      setDescription(skill?.description ?? "");
      setInstructions(skill?.instructions ?? "");
    }
  }, [open, skill]);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedDescription = description.trim();
    const trimmedInstructions = instructions.trim();
    if (!trimmedName || !trimmedInstructions) return;

    if (skill) {
      updateSkill(skill.id, {
        name: trimmedName,
        description: trimmedDescription,
        instructions: trimmedInstructions,
      });
    } else {
      createSkill({
        name: trimmedName,
        description: trimmedDescription,
        instructions: trimmedInstructions,
      });
    }
    onOpenChange(false);
  }

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="w-full max-w-[32rem] p-5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          nameRef.current?.focus();
        }}
      >
        <DialogTitle>{skill ? "Edit skill" : "Create skill"}</DialogTitle>
        <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3.5">
          <div>
            <label htmlFor="skill-name" className="text-sm font-medium text-tertiary">
              Name
            </label>
            <input
              id="skill-name"
              ref={nameRef}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Incident Investigator"
              className={fieldClass}
            />
          </div>
          <div>
            <label htmlFor="skill-description" className="text-sm font-medium text-tertiary">
              Description
            </label>
            <input
              id="skill-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Short one-line summary"
              className={fieldClass}
            />
          </div>
          <div>
            <label htmlFor="skill-instructions" className="text-sm font-medium text-tertiary">
              Instructions
            </label>
            <textarea
              id="skill-instructions"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              rows={8}
              placeholder="Describe how the assistant should approach this task…"
              className={`${fieldClass} resize-none leading-relaxed`}
            />
          </div>
          <div className="mt-1 flex justify-end gap-2">
            <button type="button" onClick={() => onOpenChange(false)} className={secondaryButtonClass}>
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim() || !instructions.trim()}
              className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-base font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
            >
              Save
            </button>
          </div>
        </form>
      </DialogContent>
    </DialogRoot>
  );
}

function DeleteSkillDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: Skill;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { state, deleteSkill } = useAppState();
  const isReferenced = Object.values(state.chats).some((chat) => chat.activeSkillId === skill.id);

  return (
    <AlertDialogRoot open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogTitle>Delete skill?</AlertDialogTitle>
        <AlertDialogDescription>
          {isReferenced
            ? "This skill is currently used by existing chats. Those chats will revert to No skill."
            : `"${skill.name}" will be removed from this prototype.`}
        </AlertDialogDescription>
        <div className="mt-5 flex justify-end gap-2">
          <AlertDialogCancel asChild>
            <button type="button" className={secondaryButtonClass}>
              Cancel
            </button>
          </AlertDialogCancel>
          <AlertDialogAction asChild>
            <button
              type="button"
              onClick={() => deleteSkill(skill.id)}
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

function SkillRow({ skill }: { skill: Skill }) {
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-subtle/50 px-3.5 py-3 transition-all duration-200 ease-premium hover:-translate-y-px hover:border-subtle hover:shadow-sm">
      <div className="min-w-0">
        <p className="text-base font-medium text-primary">{skill.name}</p>
        <p className="mt-0.5 text-sm leading-relaxed text-tertiary">{skill.description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => setEditOpen(true)}
          className="rounded-lg px-2.5 py-1.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          Edit
        </button>
        <button
          type="button"
          aria-label={`Delete ${skill.name}`}
          onClick={() => setDeleteOpen(true)}
          className="flex h-7 w-7 items-center justify-center rounded-lg text-tertiary transition-colors duration-150 hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <SkillEditorDialog skill={skill} open={editOpen} onOpenChange={setEditOpen} />
      <DeleteSkillDialog skill={skill} open={deleteOpen} onOpenChange={setDeleteOpen} />
    </div>
  );
}

export function SkillsPanel() {
  const { skillList } = useAppState();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div>
      <DialogPanelHeader
        title="Skills"
        description="Reusable behaviors chats can select."
        action={
          <button
            type="button"
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-1.5 rounded-lg border border-subtle px-2.5 py-1.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            <Plus className="h-3.5 w-3.5" />
            Create skill
          </button>
        }
      />

      <div className="mt-4 flex flex-col gap-1.5">
        {skillList.length === 0 ? (
          <p className="text-base text-tertiary">No skills yet</p>
        ) : (
          skillList.map((skill) => <SkillRow key={skill.id} skill={skill} />)
        )}
      </div>

      <SkillEditorDialog skill={null} open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}
