import { useAppState } from "../../state/AppState";
import { formatFullTimestamp } from "../../lib/format";
import type { ScheduledTask } from "../../types";

const MAX_ROWS = 8;

/** Past runs, newest first. Each row opens the message it produced rather
 * than duplicating the output here — the chat is the record. */
export function TaskRunHistory({ task }: { task: ScheduledTask }) {
  const { selectChat } = useAppState();
  const runs = [...task.runs].reverse().slice(0, MAX_ROWS);

  if (runs.length === 0) {
    return <p className="mt-2 text-base text-tertiary">No runs yet.</p>;
  }

  return (
    <div className="mt-2 flex flex-col">
      {runs.map((run) => (
        <div
          key={run.id}
          className="flex items-center justify-between gap-3 border-b border-subtle/40 py-2 last:border-b-0"
        >
          <div className="min-w-0">
            <p className="text-sm text-secondary">{formatFullTimestamp(run.startedAt)}</p>
            <p className="text-xs text-tertiary">
              {run.status === "running" ? "Running…" : (run.summary ?? "Completed")}
            </p>
          </div>
          {task.chatId && (
            <button
              type="button"
              onClick={() => selectChat(task.chatId!)}
              className="shrink-0 rounded text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
            >
              Open
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
