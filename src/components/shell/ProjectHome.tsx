import { useMemo, useState, type ReactNode } from "react";
import { Check, FileStack, MessagesSquare, MoreHorizontal, Pin, Plus, Search, Trash2, X } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { formatRelativeTime } from "../../lib/format";
import { MAX_PROJECT_FILES, countLines } from "../../lib/projectFiles";
import { useAddProjectFiles } from "../../hooks/useAddProjectFiles";
import { cn } from "../../lib/cn";
import type { Project, ProjectFile } from "../../types";
import { PromptComposer } from "../composer/PromptComposer";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";
import { Tooltip } from "../ui/Tooltip";
import { ProjectSettingsModal } from "./ProjectSettingsModal";
import { FileContentDialog } from "./FileContentDialog";
import { SidebarChatRow } from "./SidebarChatRow";

function PanelSection({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="border-b border-subtle/50 px-4 py-4 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-primary">{title}</h3>
        {actions}
      </div>
      {description && <p className="mt-1 text-xs leading-relaxed text-tertiary">{description}</p>}
      {children}
    </div>
  );
}

function fileExtensionLabel(file: ProjectFile): string {
  return file.name.split(".").pop()?.toUpperCase() ?? file.type;
}

function ProjectFilesSection({ project }: { project: Project }) {
  const { state, removeProjectFile } = useAppState();
  const files = project.fileIds.map((id) => state.projectFiles[id]).filter(Boolean);
  const { inputRef, triggerAdd, handleFilesSelected, atCapacity, notice } = useAddProjectFiles(
    project.id,
    files.length,
  );
  const [previewFile, setPreviewFile] = useState<ProjectFile | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const capacityPercent = Math.round((files.length / MAX_PROJECT_FILES) * 100);
  // Derived from the files that actually still exist rather than trusting the
  // raw id set — a selected file can be removed out from under this panel
  // (Project settings' Files list edits the same project), which would
  // otherwise leave a phantom "1 selected" bar with no matching card.
  const selectedFileIds = useMemo(
    () => files.filter((file) => selectedIds.has(file.id)).map((file) => file.id),
    [files, selectedIds],
  );
  const isSelecting = selectedFileIds.length > 0;
  const visibleFiles = query.trim()
    ? files.filter((file) => file.name.toLowerCase().includes(query.trim().toLowerCase()))
    : files;

  function closeSearch() {
    setSearchOpen(false);
    setQuery("");
  }

  function toggleSelected(fileId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) next.delete(fileId);
      else next.add(fileId);
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  function removeSelected() {
    for (const id of selectedFileIds) removeProjectFile(project.id, id);
    clearSelection();
  }

  return (
    <PanelSection
      title="Files"
      actions={
        searchOpen ? (
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") closeSearch();
              }}
              onBlur={() => {
                if (!query.trim()) closeSearch();
              }}
              placeholder="Search files"
              aria-label="Search files"
              className="w-full min-w-0 rounded-lg border border-subtle bg-surface px-2.5 py-1 text-xs text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
            />
            <IconButton label="Close search" size="sm" onClick={closeSearch}>
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        ) : (
          <div className="flex shrink-0 items-center gap-1">
            <IconButton label="Search files" size="sm" onClick={() => setSearchOpen(true)}>
              <Search className="h-3.5 w-3.5" />
            </IconButton>
            <IconButton label="Add files" size="sm" onClick={triggerAdd} disabled={atCapacity}>
              <Plus className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        )
      }
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        aria-label="Add project files"
        className="hidden"
        onChange={handleFilesSelected}
      />

      {notice && <p className="mt-2 text-xs text-warning">{notice}</p>}

      {files.length === 0 ? (
        <div className="mt-3 flex flex-col items-center gap-2 rounded-xl border border-dashed border-subtle/60 px-4 py-6 text-center">
          <FileStack className="h-5 w-5 text-tertiary" />
          <p className="text-xs leading-relaxed text-tertiary">
            Add files for chats in this project to reference.
          </p>
        </div>
      ) : (
        <>
          <div className="mt-3">
            <div className="h-1 w-full overflow-hidden rounded-full bg-surface-hover">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-200"
                style={{ width: `${capacityPercent}%` }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-tertiary">{capacityPercent}% of project capacity used</p>
          </div>

          {isSelecting && (
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                aria-label="Clear selection"
                onClick={clearSelection}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-opacity duration-150 hover:opacity-90"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <span className="flex-1 text-xs font-medium text-primary">
                {selectedFileIds.length} selected
              </span>
              <IconButton label="Remove selected" size="sm" onClick={removeSelected}>
                <Trash2 className="h-3.5 w-3.5" />
              </IconButton>
              <IconButton label="Cancel selection" size="sm" onClick={clearSelection}>
                <X className="h-3.5 w-3.5" />
              </IconButton>
            </div>
          )}

          {visibleFiles.length === 0 && (
            <p className="mt-4 text-center text-xs text-tertiary">No files match "{query.trim()}"</p>
          )}

          <div className="mt-3 grid grid-cols-2 gap-2">
            {visibleFiles.map((file) => {
              const selected = selectedIds.has(file.id);
              return (
                <div
                  key={file.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    if (isSelecting) toggleSelected(file.id);
                    else setPreviewFile(file);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      if (isSelecting) toggleSelected(file.id);
                      else setPreviewFile(file);
                    }
                  }}
                  className={cn(
                    "group relative cursor-pointer rounded-xl border p-3 text-left transition-colors duration-150",
                    selected ? "border-accent/60 bg-accent/5" : "border-subtle/50 bg-surface-hover/40 hover:bg-surface-hover",
                  )}
                >
                  {!isSelecting && (
                    <button
                      type="button"
                      aria-label={`Remove ${file.name}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        removeProjectFile(project.id, file.id);
                      }}
                      className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-md bg-surface text-tertiary opacity-0 transition-opacity duration-100 hover:text-danger focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 group-hover:opacity-100"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                  <p className="line-clamp-2 pr-4 text-xs font-medium text-primary">{file.name}</p>
                  <p className="mt-1 text-[11px] text-tertiary">{countLines(file.content)} lines</p>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="inline-flex items-center rounded-md border border-subtle px-1.5 py-0.5 text-[10px] font-medium uppercase text-tertiary">
                      {fileExtensionLabel(file)}
                    </span>
                    <button
                      type="button"
                      aria-label={selected ? `Deselect ${file.name}` : `Select ${file.name}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleSelected(file.id);
                      }}
                      className={cn(
                        "flex h-4 w-4 items-center justify-center rounded border transition-colors duration-100",
                        selected ? "border-accent bg-accent text-on-accent" : "border-subtle bg-transparent",
                      )}
                    >
                      {selected && <Check className="h-3 w-3" />}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
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
    </PanelSection>
  );
}

function ProjectSidePanel({ project }: { project: Project }) {
  const { openProjectSettings } = useAppState();
  const hasInstructions = project.instructionsText.trim().length > 0;

  return (
    <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-subtle/50 lg:block">
      <PanelSection
        title="Instructions"
        actions={
          <IconButton label="Add instructions" size="sm" onClick={() => openProjectSettings("instructions")}>
            <Plus className="h-3.5 w-3.5" />
          </IconButton>
        }
        description={hasInstructions ? undefined : "Add instructions to tailor responses in this project"}
      >
        {hasInstructions && (
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-secondary">{project.instructionsText}</p>
        )}
      </PanelSection>

      <ProjectFilesSection project={project} />
    </aside>
  );
}

export function ProjectHome({ project }: { project: Project }) {
  const { state, chatList, selectChat, openProjectSettings, closeProjectSettings, openAllProjects, toggleProjectPinned } =
    useAppState();

  const projectChats = useMemo(
    () =>
      chatList
        // Task-owned chats are surfaced as tasks in the sidebar, not as plain
        // chat rows — Recents must follow the same rule.
        .filter((chat) => chat.projectId === project.id && !chat.scheduledTaskId)
        .sort((a, b) => b.createdAt - a.createdAt),
    [chatList, project.id],
  );

  return (
    <div className="anim-fade flex h-full min-h-0 w-full">
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto py-8">
        <div className="mx-auto w-full max-w-[900px] px-6">
          <p className="truncate text-xs text-tertiary">
            <button
              type="button"
              onClick={openAllProjects}
              className="rounded hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Projects
            </button>
            <span className="mx-1">/</span> {project.name}
          </p>
          <div className="mt-2 flex items-center justify-between gap-3">
            <h1 className="min-w-0 text-2xl font-semibold text-primary">
              <ScrollingText>{project.name}</ScrollingText>
            </h1>
            <div className="flex shrink-0 items-center gap-1">
              <Tooltip label={project.pinned ? "Unpin project" : "Pin project"}>
                <IconButton
                  label={project.pinned ? "Unpin project" : "Pin project"}
                  size="sm"
                  onClick={() => toggleProjectPinned(project.id)}
                >
                  <Pin className={cn("h-4 w-4", project.pinned && "fill-current")} />
                </IconButton>
              </Tooltip>
              <Tooltip label="Project settings">
                <IconButton label="Project settings" size="sm" onClick={() => openProjectSettings()}>
                  <MoreHorizontal className="h-4 w-4" />
                </IconButton>
              </Tooltip>
            </div>
          </div>
        </div>

        <div className="mt-6">
          <PromptComposer />
        </div>

        <div className="mx-auto w-full max-w-[900px] px-6">
          {projectChats.length === 0 ? (
            <div className="mt-16 flex flex-col items-center gap-3 text-center text-tertiary">
              <MessagesSquare className="h-6 w-6" />
              <p className="max-w-xs text-sm leading-relaxed">
                Give the assistant a task and it'll pick up your project context automatically.
              </p>
            </div>
          ) : (
            <div className="mt-8">
              <p className="px-2.5 text-xs font-medium text-tertiary">Recents</p>
              <div className="mt-1.5 flex flex-col gap-0.5">
                {projectChats.map((chat) => (
                  <SidebarChatRow
                    key={chat.id}
                    chat={chat}
                    active={false}
                    onSelect={() => selectChat(chat.id)}
                    trailing={
                      <span className="ml-2 shrink-0 text-xs text-tertiary">
                        {formatRelativeTime(chat.createdAt)}
                      </span>
                    }
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <ProjectSidePanel project={project} />

      <ProjectSettingsModal
        project={project}
        open={state.projectSettingsModal.open}
        onOpenChange={(next) => (next ? openProjectSettings() : closeProjectSettings())}
      />
    </div>
  );
}
