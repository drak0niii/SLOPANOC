import { useMemo, useState } from "react";
import { CircleHelp, Pin, Plus, Search } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { formatRelativeTime } from "../../lib/format";
import { cn } from "../../lib/cn";
import { sortProjectsForDisplay } from "../../lib/projectSort";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";
import { Tooltip } from "../ui/Tooltip";

type SortMode = "updated" | "name";

export function AllProjectsPage() {
  const { projects, enterProject, toggleProjectPinned } = useAppState();
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("updated");

  const visibleProjects = useMemo(() => {
    const filtered = query.trim()
      ? projects.filter((project) => project.name.toLowerCase().includes(query.trim().toLowerCase()))
      : projects;
    return sortProjectsForDisplay(filtered, (a, b) =>
      sortMode === "name" ? a.name.localeCompare(b.name) : b.createdAt - a.createdAt,
    );
  }, [projects, query, sortMode]);

  return (
    <div className="anim-fade h-full w-full overflow-y-auto px-8 py-8">
      <div className="mx-auto w-full max-w-5xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold text-primary">Projects</h1>
          <div className="flex shrink-0 items-center gap-2">
            {searchOpen ? (
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onBlur={() => {
                  if (!query.trim()) setSearchOpen(false);
                }}
                placeholder="Search projects"
                aria-label="Search projects"
                className="w-48 rounded-lg border border-subtle bg-surface px-3 py-1.5 text-sm text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
              />
            ) : (
              <Tooltip label="Search projects">
                <IconButton label="Search projects" onClick={() => setSearchOpen(true)}>
                  <Search className="h-4 w-4" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip label="Help (coming soon)">
              <IconButton label="Help (coming soon)" disabled>
                <CircleHelp className="h-4 w-4" />
              </IconButton>
            </Tooltip>
            <div className="flex items-center gap-1.5 text-sm text-tertiary">
              <span className="hidden sm:inline">Sort by</span>
              <select
                value={sortMode}
                onChange={(event) => setSortMode(event.target.value as SortMode)}
                aria-label="Sort projects by"
                className="rounded-lg border border-subtle bg-surface px-2.5 py-1.5 text-sm font-medium text-primary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
              >
                <option value="updated">Recently created</option>
                <option value="name">Name</option>
              </select>
            </div>
            <CreateProjectDialog
              trigger={
                <button
                  type="button"
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                >
                  <Plus className="h-4 w-4" />
                  New project
                </button>
              }
            />
          </div>
        </div>

        {visibleProjects.length === 0 ? (
          <p className="mt-16 text-center text-sm text-tertiary">
            {projects.length === 0 ? "No projects yet." : "No projects match your search."}
          </p>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {visibleProjects.map((project) => (
              <div
                key={project.id}
                role="button"
                tabIndex={0}
                onClick={() => enterProject(project.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    enterProject(project.id);
                  }
                }}
                className="flex h-32 cursor-pointer flex-col rounded-2xl border border-subtle/60 p-4 text-left transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              >
                <div className="flex items-center gap-1.5">
                  <ScrollingText className="text-sm font-semibold text-primary">
                    {project.name}
                  </ScrollingText>
                  <button
                    type="button"
                    aria-label={project.pinned ? "Unpin project" : "Pin project"}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleProjectPinned(project.id);
                    }}
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors duration-150 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
                      // Ternary, not an appended override: cn() concatenates
                      // without resolving conflicts, and Tailwind emits colour
                      // utilities alphabetically — `text-tertiary` would win
                      // over `text-accent` and the pin would never highlight.
                      project.pinned ? "text-accent" : "text-tertiary",
                    )}
                  >
                    <Pin className={cn("h-3.5 w-3.5", project.pinned && "fill-current")} />
                  </button>
                </div>
                {project.description && (
                  <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-tertiary">
                    {project.description}
                  </p>
                )}
                <div className="mt-auto pt-2 text-xs text-tertiary">
                  {formatRelativeTime(project.createdAt)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
