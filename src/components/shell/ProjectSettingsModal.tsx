import { useEffect, useState } from "react";
import { File, NotebookText, Pencil, Plug, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { CONNECTOR_STATE_LABEL } from "../../data/mock";
import { CAPABILITY_LABEL } from "./settings/ConnectorsPanel";
import { cn } from "../../lib/cn";
import type { Project, ProjectFile, ProjectSettingsSection } from "../../types";
import { useAddProjectFiles } from "../../hooks/useAddProjectFiles";
import { DialogContent, DialogPanelHeader, DialogRoot, DialogTitle } from "../ui/Dialog";
import { FileContentDialog } from "./FileContentDialog";
import { DeleteProjectDialog } from "./DeleteProjectDialog";

const SECTIONS: { id: ProjectSettingsSection; label: string; icon: typeof NotebookText }[] = [
  { id: "name", label: "Details", icon: Pencil },
  { id: "instructions", label: "Instructions", icon: NotebookText },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "files", label: "Files", icon: File },
];

function Switch({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        "disabled:cursor-not-allowed disabled:opacity-40",
        checked ? "bg-accent" : "bg-surface-hover",
      )}
    >
      <span
        className={cn(
          "absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-150",
          checked && "translate-x-4",
        )}
      />
    </button>
  );
}

function InstructionsPanel({
  value,
  onChange,
  onSubmit,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div>
      <h3 className="text-base font-medium text-primary">Instructions</h3>
      <p className="mt-1 text-sm leading-relaxed text-tertiary">
        These apply automatically to every chat inside this project.
      </p>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) onSubmit();
        }}
        rows={10}
        placeholder="Describe context, terminology, or rules chats in this project should follow…"
        className="mt-3 w-full resize-none rounded-xl border border-subtle bg-surface px-3 py-2.5 text-base leading-relaxed text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
      />
    </div>
  );
}

const DESCRIPTION_LIMIT = 1000;

function ProjectNamePanel({
  value,
  onChange,
  description,
  onDescriptionChange,
  onSubmit,
}: {
  value: string;
  onChange: (value: string) => void;
  description: string;
  onDescriptionChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div>
      <h3 className="text-base font-medium text-primary">Details</h3>
      <p className="mt-1 text-sm leading-relaxed text-tertiary">
        Shown in the sidebar and everywhere else this project appears.
      </p>
      <label htmlFor="project-settings-name" className="mt-4 block text-sm font-medium text-primary">
        Title
      </label>
      <input
        id="project-settings-name"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") onSubmit();
        }}
        className="mt-1.5 w-full rounded-lg border border-subtle bg-surface px-3 py-2 text-base text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
      />

      <label
        htmlFor="project-settings-description"
        className="mt-4 block text-sm font-medium text-primary"
      >
        Description <span className="font-normal text-tertiary">(optional)</span>
      </label>
      <textarea
        id="project-settings-description"
        value={description}
        onChange={(event) => onDescriptionChange(event.target.value)}
        maxLength={DESCRIPTION_LIMIT}
        rows={4}
        placeholder="What is this project for?"
        className="mt-1.5 w-full resize-none rounded-lg border border-subtle bg-surface px-3 py-2 text-base leading-relaxed text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
      />
      <p className="mt-1 text-right text-xs text-tertiary">
        {description.length} / {DESCRIPTION_LIMIT}
      </p>
    </div>
  );
}

/** Lives in the shared footer row rather than a routed section of its own —
 * it's a single destructive action, not a settings category with content to
 * browse. Clicking it goes straight to the confirmation. */
function DeleteProjectButton({ project, onDeleted }: { project: Project; onDeleted: () => void }) {
  const { deleteProject } = useAppState();
  const [deleteOpen, setDeleteOpen] = useState(false);

  function handleConfirmDelete() {
    deleteProject(project.id);
    setDeleteOpen(false);
    onDeleted();
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setDeleteOpen(true)}
        className="inline-flex items-center justify-center rounded-full border border-danger/60 px-4 py-2 text-sm font-medium text-danger transition-colors duration-150 hover:bg-danger/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
      >
        Delete project
      </button>

      <DeleteProjectDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        projectName={project.name}
        onConfirm={handleConfirmDelete}
      />
    </>
  );
}

function ConnectorsPanel({ project }: { project: Project }) {
  const { toggleProjectConnector, connectorList } = useAppState();

  return (
    <div>
      <h3 className="text-base font-medium text-primary">Connectors</h3>
      <p className="mt-1 text-sm leading-relaxed text-tertiary">
        Choose which connectors chats in this project can use.
      </p>
      <div className="mt-3 flex flex-col gap-1.5">
        {connectorList.map((connector) => {
          const enabled = project.connectorIds.includes(connector.id);
          const connectable = connector.state === "connected";
          return (
            <div
              key={connector.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-subtle/50 px-3 py-2.5"
            >
              <div className="min-w-0">
                <p className="text-base text-primary">{connector.name}</p>
                <p className="text-sm leading-relaxed text-tertiary">{connector.purpose}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2.5">
                <span className="whitespace-nowrap text-xs text-tertiary">
                  {CAPABILITY_LABEL[connector.capability]}
                </span>
                {!connectable && (
                  <span className="whitespace-nowrap text-xs text-tertiary">
                    {CONNECTOR_STATE_LABEL[connector.state]}
                  </span>
                )}
                <Switch
                  checked={enabled}
                  disabled={!connectable}
                  onChange={() => toggleProjectConnector(project.id, connector.id)}
                  label={`${enabled ? "Disable" : "Enable"} ${connector.name} for this project`}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FilesPanel({ project }: { project: Project }) {
  const { state, removeProjectFile } = useAppState();
  const files = project.fileIds.map((id) => state.projectFiles[id]).filter(Boolean);
  const { inputRef, triggerAdd, handleFilesSelected, atCapacity, notice } = useAddProjectFiles(
    project.id,
    files.length,
  );
  const [previewFile, setPreviewFile] = useState<ProjectFile | null>(null);

  return (
    <div>
      <DialogPanelHeader
        title="Files"
        description={`Workspace files for this project. Up to 10 files.`}
        action={
          <button
            type="button"
            onClick={triggerAdd}
            disabled={atCapacity}
            className="rounded-lg border border-subtle px-2.5 py-1.5 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
          >
            Add files
          </button>
        }
      />
      <input
        ref={inputRef}
        type="file"
        multiple
        aria-label="Add project files"
        className="hidden"
        onChange={handleFilesSelected}
      />
      {notice && <p className="mt-2 text-sm text-warning">{notice}</p>}
      {files.length === 0 ? (
        <p className="mt-4 text-base text-tertiary">No files yet</p>
      ) : (
        <div className="mt-3 flex flex-col gap-1.5">
          {files.map((file) => (
            <div
              key={file.id}
              role="button"
              tabIndex={0}
              onClick={() => setPreviewFile(file)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setPreviewFile(file);
                }
              }}
              className="group flex items-center gap-2.5 rounded-lg border border-subtle/50 px-3 py-2.5 text-left transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-secondary">
                <File className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="break-words text-base text-primary">{file.name}</p>
                <p className="text-sm text-tertiary">{file.size}</p>
              </div>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  removeProjectFile(project.id, file.id);
                }}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-tertiary opacity-0 transition-opacity duration-100 hover:bg-surface-hover hover:text-danger focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 group-hover:opacity-100"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <FileContentDialog
        file={previewFile}
        open={previewFile !== null}
        onOpenChange={(next) => {
          if (!next) setPreviewFile(null);
        }}
        onRemove={() => {
          if (previewFile) removeProjectFile(project.id, previewFile.id);
        }}
      />
    </div>
  );
}

interface ProjectSettingsModalProps {
  project: Project;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ProjectSettingsModal({ project, open, onOpenChange }: ProjectSettingsModalProps) {
  const { state, setProjectSettingsSection, renameProject, setProjectInstructions } = useAppState();
  const section = state.projectSettingsModal.section;

  const [nameDraft, setNameDraft] = useState(project.name);
  const [descriptionDraft, setDescriptionDraft] = useState(project.description ?? "");
  const [instructionsDraft, setInstructionsDraft] = useState(project.instructionsText);

  useEffect(() => {
    setNameDraft(project.name);
    setDescriptionDraft(project.description ?? "");
    setInstructionsDraft(project.instructionsText);
  }, [project.id]);

  const trimmedName = nameDraft.trim();
  const isNameDirty =
    trimmedName !== project.name || descriptionDraft.trim() !== (project.description ?? "");
  const isInstructionsDirty = instructionsDraft !== project.instructionsText;
  const isDirty = section === "name" ? isNameDirty : section === "instructions" ? isInstructionsDirty : false;

  function handleCancel() {
    setNameDraft(project.name);
    setDescriptionDraft(project.description ?? "");
    setInstructionsDraft(project.instructionsText);
  }

  function handleSave() {
    if (section === "name" && trimmedName) {
      renameProject(project.id, trimmedName, descriptionDraft);
    } else if (section === "instructions") {
      setProjectInstructions(project.id, instructionsDraft);
    }
  }

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[520px] w-full max-w-[780px] flex-col overflow-hidden p-0">
        <div className="flex min-h-0 flex-1">
          <div className="flex w-52 shrink-0 flex-col border-r border-subtle/50 p-4">
            {/* Wraps rather than truncating — a project's own name is the one
                label in here that must always be readable in full. */}
            <DialogTitle className="mb-4 break-words">{project.name}</DialogTitle>
            <nav className="flex flex-col gap-0.5">
              {SECTIONS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setProjectSettingsSection(item.id)}
                  className={cn(
                    "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-left text-base transition-colors duration-150",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
                    section === item.id
                      ? "bg-surface-hover text-primary"
                      : "text-secondary hover:bg-surface-hover hover:text-primary",
                  )}
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  {item.label}
                </button>
              ))}
            </nav>
          </div>

          <div key={section} className="anim-fade min-w-0 flex-1 overflow-y-auto p-5">
            {section === "name" && (
              <ProjectNamePanel
                value={nameDraft}
                onChange={setNameDraft}
                description={descriptionDraft}
                onDescriptionChange={setDescriptionDraft}
                onSubmit={handleSave}
              />
            )}
            {section === "instructions" && (
              <InstructionsPanel value={instructionsDraft} onChange={setInstructionsDraft} onSubmit={handleSave} />
            )}
            {section === "connectors" && <ConnectorsPanel project={project} />}
            {section === "files" && <FilesPanel project={project} />}
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-between border-t border-subtle/50 px-4 py-3">
          <DeleteProjectButton project={project} onDeleted={() => onOpenChange(false)} />
          {isDirty && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCancel}
                className="inline-flex items-center justify-center rounded-full border border-subtle px-4 py-2 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={section === "name" && !trimmedName}
                className="inline-flex items-center justify-center rounded-full border border-accent/60 px-4 py-2 text-sm font-medium text-accent transition-colors duration-150 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:opacity-40"
              >
                Save
              </button>
            </div>
          )}
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
