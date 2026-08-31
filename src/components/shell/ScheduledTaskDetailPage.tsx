import { useState } from "react";
import { Pencil, Play, Trash2 } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { cn } from "../../lib/cn";
import { formatScheduleSummary } from "../../lib/scheduledTasks";
import { SOURCE_KIND_BY_ID, findDestinationByValue } from "../../data/workspaceSources";
import { ScheduledTaskDialog } from "./ScheduledTaskDialog";
import { DeleteScheduledTaskDialog } from "./DeleteScheduledTaskDialog";
import { TaskRunHistory } from "./TaskRunHistory";
import { ScrollingText } from "../ui/ScrollingText";

const PERMISSION_LABEL: Record<string, string> = {
  manual_approve: "Manually approve",
  auto_run: "Auto-run",
};

export function ScheduledTaskDetailPage() {
  const {
    state,
    openScheduledTasks,
    toggleScheduledTaskActive,
    deleteScheduledTask,
    runScheduledTask,
    selectChat,
  } = useAppState();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const task = state.activeScheduledTaskId ? state.scheduledTasks[state.activeScheduledTaskId] : null;
  if (!task) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <p className="text-sm text-tertiary">This task no longer exists.</p>
      </div>
    );
  }

  // The pending bubble waiting in the task's chat is the progress indicator —
  // this button doesn't need its own spinner.
  function handleRunNow() {
    runScheduledTask(task!.id, { focus: true });
  }

  return (
    <div className="anim-fade h-full w-full overflow-y-auto px-8 py-8">
      <div className="mx-auto w-full max-w-3xl">
        <p className="text-xs text-tertiary">
          <button
            type="button"
            onClick={openScheduledTasks}
            className="rounded font-medium hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            Scheduled tasks
          </button>
          <span className="mx-1">/</span> {task.name}
        </p>

        <div className="mt-2 flex items-center justify-between gap-3">
          <h1 className="min-w-0 text-2xl font-semibold text-primary">
            <ScrollingText>{task.name}</ScrollingText>
          </h1>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              aria-label="Edit task"
              onClick={() => setEditOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="Delete task"
              onClick={() => setDeleteOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-secondary transition-colors duration-150 hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              <Trash2 className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={handleRunNow}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              <Play className="h-3.5 w-3.5" />
              Run now
            </button>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={task.active}
            aria-label={task.active ? "Deactivate task" : "Activate task"}
            onClick={() => toggleScheduledTaskActive(task.id)}
            className={cn(
              "relative h-5 w-9 shrink-0 rounded-full transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
              task.active ? "bg-accent" : "bg-surface-hover",
            )}
          >
            <span
              className={cn(
                "absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-150",
                task.active && "translate-x-4",
              )}
            />
          </button>
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              task.active ? "bg-success/10 text-success" : "bg-surface-hover text-tertiary",
            )}
          >
            {task.active ? "Active" : "Paused"}
          </span>
          <span className="text-sm text-tertiary">
            {task.active ? formatScheduleSummary(task) : "Runs are paused"}
          </span>
          {task.chatId && (
            <button
              type="button"
              onClick={() => selectChat(task.chatId!)}
              className="rounded text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Open chat
            </button>
          )}
        </div>

        <div className="mt-8 flex flex-col gap-6 border-t border-subtle/50 pt-6">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Instructions</p>
            <p className="mt-2 whitespace-pre-wrap text-base leading-relaxed text-secondary">{task.instructions}</p>
          </div>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Repeats</p>
            <p className="mt-2 text-base font-medium text-primary">{formatScheduleSummary(task)}</p>
          </div>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Permissions</p>
            <p className="mt-2 text-base text-secondary">{PERMISSION_LABEL[task.permission]}</p>
          </div>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Sources</p>
            {task.sources.length === 0 ? (
              <p className="mt-2 text-base text-tertiary">
                No sources yet — each run has nothing to read from.
              </p>
            ) : (
              <div className="mt-2 flex flex-col gap-1">
                {task.sources.map((source) => {
                  const kind = SOURCE_KIND_BY_ID[source.kind];
                  const connected = state.connectors[kind.connectorId]?.state === "connected";
                  return (
                    <p key={source.id} className="text-base text-secondary">
                      {kind.label} · {source.scope}
                      {!connected && (
                        <span className="ml-2 text-sm text-warning">not connected</span>
                      )}
                    </p>
                  );
                })}
              </div>
            )}
          </div>

          {task.distributions.length > 0 && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-tertiary">
                Send results to
              </p>
              <div className="mt-2 flex flex-col gap-1 text-base text-secondary">
                {task.distributions.map((distribution) => (
                  <p key={`${distribution.kind}:${distribution.target}`}>
                    {findDestinationByValue(distribution.kind, distribution.target)?.label ??
                      distribution.target}
                  </p>
                ))}
              </div>
              <p className="mt-2 text-sm text-tertiary">
                Every run asks for your confirmation before anything is posted.
              </p>
            </div>
          )}

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-tertiary">Runs</p>
            <TaskRunHistory task={task} />
          </div>
        </div>
      </div>

      <ScheduledTaskDialog existingTask={task} open={editOpen} onOpenChange={setEditOpen} />

      <DeleteScheduledTaskDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        taskName={task.name}
        onConfirm={() => deleteScheduledTask(task.id)}
      />
    </div>
  );
}
