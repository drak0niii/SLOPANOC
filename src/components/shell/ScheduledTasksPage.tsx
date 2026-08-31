import { useMemo, useState, type ComponentType } from "react";
import {
  Calendar,
  CircleHelp,
  Clock,
  Lightbulb,
  ListChecks,
  Megaphone,
  Search,
  Sun,
  Telescope,
} from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { formatScheduleSummary } from "../../lib/scheduledTasks";
import type { ScheduledTaskFrequency } from "../../types";
import { IconButton } from "../ui/IconButton";
import { ScrollingText } from "../ui/ScrollingText";
import { Tooltip } from "../ui/Tooltip";

interface TaskTemplate {
  id: string;
  icon: ComponentType<{ className?: string }>;
  name: string;
  description: string;
  instructions: string;
  frequency: ScheduledTaskFrequency;
  timeOfDay: string;
}

const TEMPLATES: TaskTemplate[] = [
  {
    id: "daily-briefing",
    icon: Sun,
    name: "Daily briefing",
    description: "What needs your attention today across calendar, email, and messages.",
    instructions:
      "Give me a morning brief each weekday: what's on my calendar, important unread emails or messages, and anything that needs my attention today. Keep it short and scannable.",
    frequency: "weekdays",
    timeOfDay: "08:00",
  },
  {
    id: "inbox-triage",
    icon: Megaphone,
    name: "Inbox triage",
    description: "Categorize your inbox and draft replies to anything urgent.",
    instructions: "Categorize my inbox and draft replies to anything urgent.",
    frequency: "weekdays",
    timeOfDay: "08:00",
  },
  {
    id: "meeting-prep",
    icon: Calendar,
    name: "Meeting prep",
    description: "A short brief before each meeting on your calendar, covering attendees, context, and agenda.",
    instructions:
      "Give me a short brief before each meeting on my calendar, covering attendees, context, and agenda.",
    frequency: "weekdays",
    timeOfDay: "08:00",
  },
  {
    id: "weekly-review",
    icon: ListChecks,
    name: "Weekly review",
    description: "A Friday summary of what happened this week.",
    instructions: "Summarize what happened this week.",
    frequency: "weekly",
    timeOfDay: "16:00",
  },
  {
    id: "content-ideas",
    icon: Lightbulb,
    name: "Content ideas",
    description: "Draft a few post ideas each week from the latest news in your industry.",
    instructions: "Draft a few social post ideas based on the latest news in my industry.",
    frequency: "weekly",
    timeOfDay: "09:00",
  },
  {
    id: "monitor-topic",
    icon: Telescope,
    name: "Monitor a topic",
    description: "Watch for news or mentions of a topic, competitor, or keyword.",
    instructions: "Watch for news or mentions of a specific topic, competitor, or keyword and summarize what you find.",
    frequency: "daily",
    timeOfDay: "09:00",
  },
];

type SortMode = "recent" | "name";

export function ScheduledTasksPage() {
  const { state, projects, viewScheduledTask, startScheduledTaskSetup } = useAppState();
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("recent");

  const tasks = useMemo(() => {
    const all = Object.values(state.scheduledTasks);
    const filtered = query.trim()
      ? all.filter((task) => task.name.toLowerCase().includes(query.trim().toLowerCase()))
      : all;
    return [...filtered].sort((a, b) =>
      sortMode === "name" ? a.name.localeCompare(b.name) : b.createdAt - a.createdAt,
    );
  }, [state.scheduledTasks, query, sortMode]);

  /** Templates seed the scheduling conversation rather than opening a second
   * creation form — one way to create a task, not two. */
  function startFromTemplate(template: TaskTemplate) {
    startScheduledTaskSetup({ seedPrompt: template.instructions });
  }

  return (
    <div className="anim-fade h-full w-full overflow-y-auto px-8 py-8">
      <div className="mx-auto w-full max-w-5xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-primary">Scheduled tasks</h1>
            <p className="mt-1 text-sm text-tertiary">Run tasks on a schedule or whenever you need them.</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {searchOpen ? (
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onBlur={() => {
                  if (!query.trim()) setSearchOpen(false);
                }}
                placeholder="Search tasks"
                aria-label="Search scheduled tasks"
                className="w-48 rounded-lg border border-subtle bg-surface px-3 py-1.5 text-sm text-primary placeholder:text-tertiary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
              />
            ) : (
              <Tooltip label="Search tasks">
                <IconButton label="Search tasks" onClick={() => setSearchOpen(true)}>
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
                aria-label="Sort tasks by"
                className="rounded-lg border border-subtle bg-surface px-2.5 py-1.5 text-sm font-medium text-primary transition-colors duration-150 focus:border-accent/40 focus:outline-none"
              >
                <option value="recent">Recently created</option>
                <option value="name">Name</option>
              </select>
            </div>
            <button
              type="button"
              onClick={() => startScheduledTaskSetup()}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              New task
            </button>
          </div>
        </div>

        {tasks.length > 0 && (
          <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {tasks.map((task) => {
              const project = task.projectId ? projects.find((p) => p.id === task.projectId) : null;
              return (
                <div
                  key={task.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => viewScheduledTask(task.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      viewScheduledTask(task.id);
                    }
                  }}
                  className="cursor-pointer rounded-2xl border border-subtle/60 p-4 transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                >
                  <ScrollingText className="text-sm font-semibold text-primary">
                    {task.name}
                  </ScrollingText>
                  <p className="mt-1 line-clamp-2 text-sm text-tertiary">{task.instructions}</p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span
                      className={cnBadge(task.active)}
                    >
                      {task.active ? formatScheduleSummary(task) : "Paused"}
                    </span>
                    {project && (
                      <span className="flex min-w-0 items-center gap-1 truncate text-xs text-tertiary">
                        <Calendar className="h-3 w-3 shrink-0" />
                        <span className="truncate">{project.name}</span>
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-8 border-t border-subtle/50 pt-6">
          <p className="text-sm font-medium text-tertiary">Templates to get started</p>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {TEMPLATES.map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                onClick={() => startFromTemplate(tpl)}
                className="flex items-start gap-3 rounded-2xl border border-subtle/60 p-4 text-left transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-secondary">
                  <tpl.icon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-primary">{tpl.name}</p>
                  <p className="mt-0.5 text-sm leading-relaxed text-tertiary">{tpl.description}</p>
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-tertiary">
                    <Clock className="h-3 w-3" />
                    {formatScheduleSummary(tpl)}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

    </div>
  );
}

function cnBadge(active: boolean) {
  return active
    ? "rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success"
    : "rounded-full bg-surface-hover px-2 py-0.5 text-xs font-medium text-tertiary";
}
